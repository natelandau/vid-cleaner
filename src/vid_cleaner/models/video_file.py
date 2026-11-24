"""VideoFile model."""

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cappa
from box import Box
from ffmpeg_progress_yield import FfmpegProgress
from iso639 import Lang
from iso639.exceptions import DeprecatedLanguageValue, InvalidLanguageValue

from vid_cleaner.config import Config, RunOptions
from vid_cleaner.constants import (
    COMMENTARY_STREAM_TITLE_REGEX,
    DOWNMIX_STEREO_FILTER,
    DROP_SUBTITLES_LABEL,
    EXCLUDED_VIDEO_CODECS,
    FFMPEG_APPEND,
    FFMPEG_PREPEND,
    FHD_RESOLUTION,
    H265_CODECS,
    HDTV_RESOLUTION,
    MIN_SURROUND_CHANNELS,
    SDTV_RESOLUTION,
    TEXT_SUBTITLE_CODECS,
    UHDTV_RESOLUTION,
    AudioLayout,
    CodecTypes,
    VideoTrait,
)
from vid_cleaner.exceptions import VideoCleanError
from vid_cleaner.models.conversion_plan import ConversionPlan, OutputStream, PlanAction
from vid_cleaner.utils import (
    MediaId,
    find_media_ids,
    get_probe_as_box,
    query_radarr,
    query_sonarr,
    query_tmdb,
    query_tmdb_by_id,
    run_ffprobe,
)

from vid_cleaner.controllers import TempFile  # isort: skip


@dataclass(frozen=True)
class FfmpegCommand:
    """A complete ffmpeg command line and the temporary file it writes.

    Attributes:
        arguments (list[str]): The command line, starting with `ffmpeg`.
        output_path (Path): The temporary file the command writes.
    """

    arguments: list[str]
    output_path: Path


class VideoFile:
    """VideoFile model."""

    def __init__(self, path: Path) -> None:
        """Initialize VideoFile."""
        self.path = path.expanduser().resolve()
        self.name = path.name
        self.stem = path.stem
        self.parent = path.parent
        self.suffix = path.suffix
        self.suffixes = self.path.suffixes
        self.temp_file = TempFile(self.path)

        self.container = self.suffix
        self.language: Lang | None = None
        self.ran_language_check = False
        self._probe_box: Box = Box({}, default_box=True, default_box_create_on_get=False)

        self._all_streams: list[Box] = []
        self._video_streams: list[Box] = []
        self._audio_streams: list[Box] = []
        self._subtitle_streams: list[Box] = []

    @property
    def probe_box(self) -> Box:
        """Get the probe box."""
        if self._probe_box.path_to_file != self.temp_file.latest_temp_path():
            self._probe_box = get_probe_as_box(self.temp_file.latest_temp_path())

        return self._probe_box

    @property
    def video_streams(self) -> list[Box]:
        """Get the video streams."""
        if not self._video_streams:
            self._video_streams = [
                s
                for s in self.probe_box.streams
                if s.codec_type == CodecTypes.VIDEO
                and s.codec_name.lower() not in EXCLUDED_VIDEO_CODECS
            ]
        return self._video_streams

    @property
    def audio_streams(self) -> list[Box]:
        """Get the audio streams."""
        if not self._audio_streams:
            self._audio_streams = [
                s for s in self.probe_box.streams if s.codec_type == CodecTypes.AUDIO
            ]
        return self._audio_streams

    @property
    def subtitle_streams(self) -> list[Box]:
        """Get the subtitle streams."""
        if not self._subtitle_streams:
            self._subtitle_streams = [
                s for s in self.probe_box.streams if s.codec_type == CodecTypes.SUBTITLE
            ]
        return self._subtitle_streams

    @property
    def all_streams(self) -> list[Box]:
        """Get all streams."""
        if not self._all_streams:
            self._all_streams = self.video_streams + self.audio_streams + self.subtitle_streams
        return self._all_streams

    def get_traits(self) -> list[VideoTrait]:
        """Analyze video file streams to identify audio, video, and structural characteristics.

        Extract comprehensive traits from the video file by examining audio streams for channel layouts and commentary tracks, video streams for codec types and resolutions, and derive additional traits based on stream ordering requirements and audio configuration gaps.

        Returns:
            list[VideoTrait]: A list of VideoTrait enums representing all identified characteristics including audio layouts, video codecs, resolutions, and structural properties.
        """
        traits = []

        # Process audio streams
        traits.extend(self._get_audio_traits())

        # Process video streams
        traits.extend(self._get_video_traits())

        # Add derived traits
        if self._needs_stereo():
            traits.append(VideoTrait.NEEDS_STEREO)

        if self._need_stream_reorder():
            traits.append(VideoTrait.REORDER)

        return traits

    def _get_audio_traits(self) -> list[VideoTrait]:
        """Extract audio-related traits from the video file's audio streams.

        Analyze each audio stream to identify characteristics such as channel layout
        (stereo, mono, surround sound) and special properties like commentary tracks.
        Commentary tracks are identified by matching stream titles against a regex pattern.

        Returns:
            list[VideoTrait]: A list of VideoTrait enums representing the audio characteristics
                found in the file's audio streams.
        """
        traits = []
        for stream in self.audio_streams:
            if self._is_commentary_stream(stream):
                traits.append(VideoTrait.COMMENTARY)
            elif stream.channels == AudioLayout.STEREO:
                traits.append(VideoTrait.STEREO)
            elif stream.channels == AudioLayout.MONO:
                traits.append(VideoTrait.MONO)
            elif stream.channels == AudioLayout.SURROUND5:
                traits.append(VideoTrait.SURROUND5)
            elif stream.channels == AudioLayout.SURROUND7:
                traits.append(VideoTrait.SURROUND7)
        return traits

    def _get_video_traits(self) -> list[VideoTrait]:
        """Extract video-related traits from the video file's video streams.

        Analyze each video stream to identify codec type (H.264, H.265) and resolution
        characteristics (HDTV, FHD, UHDTV, SDTV). Resolution is determined by comparing
        both height and width against standard resolution constants.

        Returns:
            list[VideoTrait]: A list of VideoTrait enums representing the video characteristics
                found in the file's video streams, including codec and resolution information.
        """
        traits = []
        for stream in self.video_streams:
            if stream.codec_name.lower() in H265_CODECS:
                traits.append(VideoTrait.H265)
            elif stream.codec_name.lower() == "h264":
                traits.append(VideoTrait.H264)

            if stream.height == HDTV_RESOLUTION.height or stream.width == HDTV_RESOLUTION.width:
                traits.append(VideoTrait.HDTV)
            elif stream.height == FHD_RESOLUTION.height or stream.width == FHD_RESOLUTION.width:
                traits.append(VideoTrait.FHD)
            elif stream.height == UHDTV_RESOLUTION.height or stream.width == UHDTV_RESOLUTION.width:
                traits.append(VideoTrait.UHDTV)
            elif stream.height == SDTV_RESOLUTION.height or stream.width == SDTV_RESOLUTION.width:
                traits.append(VideoTrait.SDTV)
            else:
                traits.append(VideoTrait.UNKNOWN_RESOLUTION)
        return traits

    @staticmethod
    def _is_commentary_stream(stream: Box) -> bool:
        """Check whether a stream is a commentary, SDH, or description track.

        Identify supplementary tracks by matching the stream title against the commentary regex so callers can treat them differently from main content.

        Args:
            stream (Box): The stream to inspect.

        Returns:
            bool: True if the stream title marks it as commentary/SDH/description.
        """
        return bool(
            stream.title and re.search(COMMENTARY_STREAM_TITLE_REGEX, stream.title, re.IGNORECASE)
        )

    @staticmethod
    def _stream_language(stream: Box) -> Lang | None:
        """Parse a stream's language tag so it can be compared with the keep languages.

        Files carry tags that are not valid ISO 639 codes, such as `qaa`, uppercase `ENG`, or
        deprecated `iw`. Treat those as unknown rather than letting one bad tag abort the run.

        Args:
            stream (Box): The stream to inspect.

        Returns:
            Lang | None: The tagged language, or None when the tag is missing, `und`, or not a
                usable ISO 639 code.
        """
        if not stream.language:
            return None
        tag = str(stream.language).lower()
        if tag == "und":
            return None
        # `_note_unusable_language_tags` reports these tags in the plan's debug notes
        try:
            return Lang(tag)
        except (InvalidLanguageValue, DeprecatedLanguageValue):
            return None

    def _note_unusable_language_tags(self, plan: ConversionPlan) -> None:
        """Record each audio or subtitle language tag that planning treats as unknown.

        Args:
            plan (ConversionPlan): The plan whose debug notes receive the findings.
        """
        plan.debug_notes.extend(
            f"Stream #{stream.index}: unusable language tag '{stream.language}', treated as unknown"
            for stream in self.audio_streams + self.subtitle_streams
            if stream.language
            and str(stream.language).lower() != "und"
            and self._stream_language(stream) is None
        )

    @staticmethod
    def _is_surround_stream(stream: Box) -> bool:
        """Check whether a stream carries a surround bed a stereo mix can be downmixed from.

        Read the raw channel count rather than the bucketed `AudioLayout`, which is None for
        Atmos (>7.1) tracks that downmix perfectly well.

        Args:
            stream (Box): The stream to inspect.

        Returns:
            bool: True if the stream has enough channels to seed a downmix.
        """
        return bool(stream.channel_count) and stream.channel_count >= MIN_SURROUND_CHANNELS

    def _needs_stereo(self) -> bool:
        """Check whether the file lacks a stereo mix but could have one built from its audio.

        Distinguishes a file worth downmixing from one that merely has no stereo track, such as
        an original mono recording, which has nothing to downmix and should be left alone. The
        commentary handling mirrors `_plan_downmix` so the trait never promises a stereo track
        the planner would decline to build.

        Returns:
            bool: True if no non-commentary stereo mix exists and a surround bed does.
        """
        has_stereo_mix = any(
            stream.channels == AudioLayout.STEREO and not self._is_commentary_stream(stream)
            for stream in self.audio_streams
        )
        return not has_stereo_mix and any(
            self._is_surround_stream(stream) for stream in self.audio_streams
        )

    def _plan_video_streams(self) -> list[OutputStream]:
        """Plan the video streams to keep as passthrough copies.

        Returns:
            list[OutputStream]: One copy-mapped output per kept video stream.
        """
        return [
            OutputStream(source_index=stream.index, codec_type=CodecTypes.VIDEO)
            for stream in self.video_streams
        ]

    @staticmethod
    def _downmix_source_rank(stream: Box, langs: list[Lang]) -> tuple[bool, int, int]:
        """Rank a surround stream as a downmix source; lower sorts first.

        Commentary is never a mix of the main audio, so it ranks last. Among the rest,
        the configured keep languages win in their listed order, then untagged or `und`
        streams, then any other language. Ties fall to the simplest layout, since a 5.1
        bed downmixes more predictably than 7.1 or Atmos.

        Args:
            stream (Box): A surround audio stream.
            langs (list[Lang]): Keep languages in preference order.

        Returns:
            tuple[bool, int, int]: Sort key of (is commentary, language rank, layout tier).
        """
        unknown_rank = len(langs)
        lang = VideoFile._stream_language(stream)
        if lang is None:
            lang_rank = unknown_rank
        else:
            lang_rank = langs.index(lang) if lang in langs else unknown_rank + 1

        if stream.channel_count <= AudioLayout.SURROUND5.value:
            tier = 0
        elif stream.channel_count <= AudioLayout.SURROUND7.value:
            tier = 1
        else:
            tier = 2

        return (VideoFile._is_commentary_stream(stream), lang_rank, tier)

    @staticmethod
    def _plan_downmix(
        streams: list[Box], langs: list[Lang], *, force: bool
    ) -> tuple[list[OutputStream], list[Box], PlanAction | None]:
        """Plan a downmix of the best surround bed to a dialogue-forward stereo track.

        Skip the work when a non-commentary stereo mix already exists, unless
        `force` is set, in which case the existing stereo track(s) in the surround
        bed's language are dropped and rebuilt from that bed. Stereo tracks in other
        languages are kept, since the single rebuilt track does not replace them.
        The returned action says why a downmix was skipped.

        Args:
            streams (list[Box]): Audio streams that would otherwise be kept.
            langs (list[Lang]): Keep languages in preference order, used to choose
                between surround sources.
            force (bool): Rebuild the stereo track even when one already exists.

        Returns:
            tuple[list[OutputStream], list[Box], PlanAction | None]: The planned downmix
                output streams, the existing streams the caller must not map, and the
                recorded action (always populated; callers only invoke this method when
                `downmix_stereo` is set).
        """
        downmix_streams: list[OutputStream] = []
        streams_to_drop: list[Box] = []

        # Commentary tracks are often stereo but are not a stereo mix of the main audio,
        # so they must not count as an existing stereo mix.
        existing_stereo = [
            stream
            for stream in streams
            if stream.channels == AudioLayout.STEREO and not VideoFile._is_commentary_stream(stream)
        ]

        # One stereo track per file: rank every surround bed and keep the best. `min` returns
        # the first of equal ranks, so file order breaks ties.
        surround = [s for s in streams if VideoFile._is_surround_stream(s)]
        surround_source = min(
            surround,
            key=lambda s: VideoFile._downmix_source_rank(stream=s, langs=langs),
            default=None,
        )

        if existing_stereo:
            if not force:
                action = PlanAction(
                    label="Downmix to stereo",
                    applied=False,
                    reason="stereo track already exists; use --force",
                )
                return downmix_streams, streams_to_drop, action
            if surround_source is None:
                action = PlanAction(
                    label="Downmix to stereo", applied=False, reason="no surround source to downmix"
                )
                return downmix_streams, streams_to_drop, action
            # Forced recreation: drop the existing stereo mix and rebuild it from the surround bed
            source_language = VideoFile._stream_language(surround_source)
            streams_to_drop = [
                stream
                for stream in existing_stereo
                if VideoFile._stream_language(stream) == source_language
            ]

        if surround_source is not None:
            downmix_streams.append(
                OutputStream(
                    source_index=surround_source.index,
                    codec_type=CodecTypes.AUDIO,
                    codec="aac",
                    stream_filter=DOWNMIX_STEREO_FILTER,
                    extra_args=["-ac:a:{n}", "2", "-b:a:{n}", "256k", "-ar:a:{n}", "48000"],
                    metadata={"title": "2.0"},
                )
            )
        action = PlanAction(
            label="Downmix to stereo",
            applied=bool(downmix_streams),
            reason=None if downmix_streams else "no surround source to downmix",
        )
        return downmix_streams, streams_to_drop, action

    def _plan_audio_streams(
        self, plan: ConversionPlan, config: Config, options: RunOptions
    ) -> list[OutputStream]:
        """Plan audio streams to keep, honoring language, commentary, and downmix settings.

        Args:
            plan (ConversionPlan): The plan whose actions record drop-audio and downmix
                outcomes.
            config (Config): The settings for this run.
            options (RunOptions): The CLI-only values for this run.

        Returns:
            list[OutputStream]: Copy-mapped kept audio followed by any planned downmix.
        """
        langs = [Lang(lang) for lang in config.langs_to_keep]

        # Add original language to list of languages to keep if not explicitly dropping it
        if not config.drop_original_audio:
            original_language = self._find_original_language(config, notes=plan.debug_notes)
            if original_language and original_language not in langs:
                langs.append(original_language)

        streams_to_keep: list[Box] = []
        for stream in self.audio_streams:
            # Commentary tracks are often unwanted and take up space
            if not config.keep_commentary and self._is_commentary_stream(stream):
                plan.debug_notes.append(f"PLAN AUDIO: Remove stream #{stream.index} [commentary]")
                continue

            # Unknown language streams are kept to avoid removing potentially important audio
            language = self._stream_language(stream)
            if language is None or language in langs:
                streams_to_keep.append(stream)
                continue

            plan.debug_notes.append(f"PLAN AUDIO: Remove stream #{stream.index}")

        # If every stream would be removed, keep them all to prevent silent video
        fallback_triggered = not streams_to_keep
        if fallback_triggered:
            streams_to_keep = list(self.audio_streams)

        dropped = len(self.audio_streams) - len(streams_to_keep)
        if fallback_triggered:
            action = PlanAction(
                label="Drop unwanted audio",
                applied=False,
                reason="kept all audio to avoid a silent file",
            )
        else:
            action = PlanAction(
                label="Drop unwanted audio",
                applied=dropped > 0,
                reason=None if dropped > 0 else "all audio matches keep languages",
            )
        plan.actions.append(action)

        # Plan the downmix; forced recreation can request dropping an existing stereo track
        downmix_streams, streams_to_drop, downmix_action = (
            self._plan_downmix(streams=streams_to_keep, langs=langs, force=options.force)
            if config.downmix_stereo
            else ([], [], None)
        )
        if downmix_action is not None:
            plan.actions.append(downmix_action)

        drop_indices = {stream.index for stream in streams_to_drop}
        kept = [
            OutputStream(source_index=stream.index, codec_type=CodecTypes.AUDIO)
            for stream in streams_to_keep
            if stream.index not in drop_indices
        ]
        return kept + downmix_streams

    @staticmethod
    def _should_keep_subtitle(
        stream: Box, langs: list[Lang], original_language: Lang | None, config: Config
    ) -> bool:
        """Decide whether a single subtitle stream matches the user's keep preferences.

        Args:
            stream (Box): The subtitle stream under evaluation.
            langs (list[Lang]): Languages the user wants to keep.
            original_language (Lang | None): The file's original audio language, or
                None when `drop_local_subs` made it unnecessary to look up.
            config (Config): The settings for this run.

        Returns:
            bool: True when the stream should be mapped into the output.
        """
        if config.keep_all_subtitles:
            return True

        if not stream.language:
            return False

        # Undefined-language streams count as a match so important subtitles are not removed
        language = VideoFile._stream_language(stream)
        matches_keep_languages = str(stream.language).lower() == "und" or (
            language is not None and language in langs
        )

        if config.keep_local_subtitles and matches_keep_languages:
            return True

        # Keep subtitles in user's languages when original audio differs
        # This ensures subtitles are available when needed for translation
        return bool(
            not config.drop_local_subs
            and langs
            and original_language not in langs
            and matches_keep_languages
        )

    def _plan_subtitle_streams(self, plan: ConversionPlan, config: Config) -> list[OutputStream]:
        """Plan subtitle streams to keep based on language preferences.

        Args:
            plan (ConversionPlan): The plan whose actions record the drop-subtitles
                outcome.
            config (Config): The settings for this run.

        Returns:
            list[OutputStream]: Copy-mapped kept subtitle streams.
        """
        keep: list[OutputStream] = []

        langs = [Lang(lang) for lang in config.langs_to_keep]

        # Only look up original language if we're not explicitly dropping local subs
        # This avoids unnecessary API calls
        original_language = (
            None
            if config.drop_local_subs
            else self._find_original_language(config, notes=plan.debug_notes)
        )

        # Skip evaluating streams entirely when settings drop every local subtitle;
        # a single return path below still records the drop-subtitles action.
        drop_all_local = (
            not config.keep_all_subtitles
            and not config.keep_local_subtitles
            and config.drop_local_subs
        )

        if not drop_all_local:
            for stream in self.subtitle_streams:
                # Remove commentary/SDH/description tracks unless explicitly kept
                # These are typically supplementary and take up extra space
                if not config.keep_commentary and self._is_commentary_stream(stream):
                    plan.debug_notes.append(
                        f"PLAN SUBTITLES: Remove stream #{stream.index} [commentary]"
                    )
                    continue

                if self._should_keep_subtitle(
                    stream=stream, langs=langs, original_language=original_language, config=config
                ):
                    keep.append(
                        OutputStream(source_index=stream.index, codec_type=CodecTypes.SUBTITLE)
                    )
                    continue

                plan.debug_notes.append(f"PLAN SUBTITLES: Remove stream #{stream.index}")

        dropped = len(self.subtitle_streams) - len(keep)
        if dropped > 0:
            reason: str | None = None
        elif not self.subtitle_streams:
            reason = "no subtitles to drop"
        elif config.keep_all_subtitles:
            reason = "--keep-all-subtitles set"
        else:
            reason = "no unwanted subtitles"
        plan.actions.append(
            PlanAction(label=DROP_SUBTITLES_LABEL, applied=dropped > 0, reason=reason)
        )

        return keep

    def _first_video_stream(self) -> Box | None:
        """Find the first processable video stream in the probe data.

        Returns:
            Box | None: The first non-thumbnail video stream, or None when absent.
        """
        # video_streams already applies the non-thumbnail filter and is memoized.
        return self.video_streams[0] if self.video_streams else None

    def _estimate_cleaned_size_mb(self, mapped_indices: set[int], duration: float) -> float:
        """Estimate the input size in megabytes after dropped streams are removed.

        The single-pass encode has no cleaned intermediate file to measure, so subtract
        each dropped stream's bitrate-derived size from the original file. Streams with
        no discoverable bitrate contribute zero, erring toward a higher target.

        Args:
            mapped_indices (set[int]): Input stream indices kept in the output.
            duration (float): Stream duration in seconds.

        Returns:
            float: Estimated cleaned size in MB, floored at 10% of the original file so
                bogus metadata can never produce a non-positive size.
        """
        file_size = self.temp_file.latest_temp_path().stat().st_size

        dropped_bytes = 0.0
        for stream in self.probe_box.streams:
            if stream.index in mapped_indices:
                continue
            bit_rate = stream.bit_rate or stream.bps
            if bit_rate:
                dropped_bytes += int(bit_rate) / 8 * duration

        return max(file_size - dropped_bytes, file_size * 0.1) / 1_000_000

    def _plan_h265(self, plan: ConversionPlan, options: RunOptions) -> None:
        """Plan an H.265 encode of the video stream with size-derived bitrate targets.

        Args:
            plan (ConversionPlan): The plan whose video stream is annotated in place.
            options (RunOptions): The CLI-only values for this run.
        """
        video_stream = self._first_video_stream()
        if not video_stream:
            plan.actions.append(
                PlanAction(label="Convert to H.265", applied=False, reason="no video stream")
            )
            return

        if not options.force and video_stream.codec_name.lower() in H265_CODECS:
            plan.actions.append(
                PlanAction(
                    label="Convert to H.265", applied=False, reason="already H.265/VP9; use --force"
                )
            )
            return

        # Calculate target bitrate using Frame.io's formula: https://blog.frame.io/2017/03/06/calculate-video-bitrates/
        # This formula provides good quality while maintaining reasonable file sizes
        stream_duration = float(self.probe_box.duration or 0) or float(video_stream.duration or 0)
        if not stream_duration:
            plan.actions.append(
                PlanAction(
                    label="Convert to H.265", applied=False, reason="could not determine duration"
                )
            )
            return

        # Convert duration to minutes for bitrate calculation
        duration = stream_duration * 0.0166667

        mapped_indices = {stream.source_index for stream in plan.streams}
        file_size_megabytes = self._estimate_cleaned_size_mb(
            mapped_indices=mapped_indices, duration=stream_duration
        )

        # When the same pass also downscales, the encode sees fewer pixels than the
        # source, so shrink the size-derived targets by the pixel ratio.
        width = getattr(video_stream, "width", 0) or 0
        scale_factor = (1920 / width) ** 2 if options.video_1080 and width > 1920 else 1.0  # noqa: PLR2004

        # Calculate bitrates with a target of 50% of original size while maintaining quality
        current_bitrate = int(file_size_megabytes / (duration * 0.0075) * scale_factor)
        target_bitrate = int(current_bitrate / 2)
        # Allow 30% variance from target bitrate to handle complex scenes
        min_bitrate = int(current_bitrate * 0.7)
        max_bitrate = int(current_bitrate * 1.3)

        # Encode every kept video stream, matching the old `-c:v libx265` (no index),
        # which applied to all video streams, not just the first.
        for video_output in [s for s in plan.streams if s.codec_type == CodecTypes.VIDEO]:
            video_output.codec = "libx265"
            video_output.extra_args = [
                "-b:v:{n}",
                f"{target_bitrate}k",
                "-minrate:v:{n}",
                f"{min_bitrate}k",
                "-maxrate:v:{n}",
                f"{max_bitrate}k",
                "-bufsize:v:{n}",
                f"{current_bitrate}k",
            ]
        plan.actions.append(PlanAction(label="Convert to H.265", applied=True))

    def _plan_vp9(self, plan: ConversionPlan, options: RunOptions) -> None:
        """Plan a VP9/WebM conversion, adapting audio and subtitles to the container.

        WebM only carries Vorbis/Opus audio and WebVTT subtitles, so every kept audio
        stream (including a planned downmix) targets libvorbis, text subtitles convert
        to WebVTT, and image-based subtitles are dropped with a notice.

        Args:
            plan (ConversionPlan): The plan mutated in place.
            options (RunOptions): The CLI-only values for this run.
        """
        video_stream = self._first_video_stream()
        if not video_stream:
            plan.actions.append(
                PlanAction(label="Convert to VP9", applied=False, reason="no video stream")
            )
            return

        # Skip re-encoding if already in modern codec unless forced
        if not options.force and video_stream.codec_name.lower() in H265_CODECS:
            plan.actions.append(
                PlanAction(
                    label="Convert to VP9", applied=False, reason="already H.265/VP9; use --force"
                )
            )
            return

        destination = options.out or self.path
        if destination.suffix != ".webm":
            plan.notices.append(
                f"Converting to VP9, setting output to `{destination.with_suffix('.webm').name}`"
            )
        plan.output_suffix = ".webm"
        # Data streams and chapters may corrupt WebM output
        plan.global_args.extend(["-dn", "-map_chapters", "-1"])

        codec_names = {stream.index: stream.codec_name.lower() for stream in self.probe_box.streams}
        kept_streams: list[OutputStream] = []
        for stream in plan.streams:
            if stream.codec_type == CodecTypes.VIDEO:
                stream.codec = "libvpx-vp9"
                # Constant quality encoding (CRF) instead of bitrate for better quality control
                stream.extra_args = ["-b:v:{n}", "0", "-crf:v:{n}", "30"]
            elif stream.codec_type == CodecTypes.AUDIO:
                stream.codec = "libvorbis"
            elif stream.codec_type == CodecTypes.SUBTITLE:
                if codec_names.get(stream.source_index) in TEXT_SUBTITLE_CODECS:
                    stream.codec = "webvtt"
                else:
                    plan.notices.append(
                        f"Dropping image-based subtitle stream 0:{stream.source_index}; WebM only supports WebVTT"
                    )
                    self._mark_subtitles_dropped(plan)
                    continue
            kept_streams.append(stream)

        plan.streams = kept_streams
        plan.actions.append(PlanAction(label="Convert to VP9", applied=True))

    @staticmethod
    def _mark_subtitles_dropped(plan: ConversionPlan) -> None:
        """Correct the "Drop unwanted subtitles" action once VP9 drops an image subtitle.

        `_plan_subtitle_streams` runs before VP9 adaptation and may have recorded
        applied=False; a VP9-only drop makes that earlier outcome untruthful.

        Args:
            plan (ConversionPlan): The plan whose recorded action is updated in place.
        """
        for action in plan.actions:
            if action.label == DROP_SUBTITLES_LABEL:
                action.applied = True
                action.reason = None

    def _plan_scale_to_1080p(self, plan: ConversionPlan, options: RunOptions) -> None:
        """Plan downscaling the video stream to 1080p.

        Args:
            plan (ConversionPlan): The plan whose video stream is annotated in place.
            options (RunOptions): The CLI-only values for this run.
        """
        video_stream = self._first_video_stream()
        if not video_stream:
            plan.actions.append(
                PlanAction(label="Convert to 1080p", applied=False, reason="no video stream")
            )
            return

        # Skip downscaling if video is already 1080p or smaller, unless forced
        if not options.force and (getattr(video_stream, "width", 0) or 0) <= 1920:  # noqa: PLR2004
            plan.actions.append(
                PlanAction(label="Convert to 1080p", applied=False, reason="source already ≤1080p")
            )
            return

        # Scale every kept video stream, matching the old `-filter:v scale=...` (no index),
        # which applied to all video streams, not just the first.
        for video_output in [s for s in plan.streams if s.codec_type == CodecTypes.VIDEO]:
            # Use -2 for height to maintain aspect ratio while ensuring even dimensions for compatibility
            video_output.stream_filter = "scale=width=1920:height=-2"
            if video_output.codec == "copy":
                # Scaling requires an encode; None lets ffmpeg pick the container default
                video_output.codec = None
        plan.actions.append(PlanAction(label="Convert to 1080p", applied=True))

    def _build_plan(self, config: Config, options: RunOptions) -> ConversionPlan:
        """Compose every requested operation into a single-pass conversion plan.

        Args:
            config (Config): The settings for this run.
            options (RunOptions): The CLI-only values for this run.

        Returns:
            ConversionPlan: The composed plan, ready to build one ffmpeg command.
        """
        plan = ConversionPlan()
        video = self._plan_video_streams()
        audio = self._plan_audio_streams(plan=plan, config=config, options=options)
        subtitles = self._plan_subtitle_streams(plan=plan, config=config)
        plan.streams = video + audio + subtitles

        # Codec planners run before the scale planner so scaling only falls back to the
        # container default encoder when no explicit codec was requested.
        if options.h265:
            self._plan_h265(plan=plan, options=options)
        if options.vp9:
            self._plan_vp9(plan=plan, options=options)
        if options.video_1080:
            self._plan_scale_to_1080p(plan=plan, options=options)

        return plan

    def plan_clean(self, config: Config, options: RunOptions) -> ConversionPlan:
        """Plan every requested cleaning operation as a single ffmpeg pass.

        Compose stream selection, reordering, downmix, scaling, and codec conversion
        into one plan so the file is decoded and written exactly once. The plan carries
        everything the caller reports: its actions, notices, and debug notes.

        Args:
            config (Config): The settings for this run.
            options (RunOptions): The CLI-only values for this run.

        Returns:
            ConversionPlan: The plan, whose actions record each operation applied or
                skipped and whose `output_suffix` names any container change.

        Raises:
            VideoCleanError: If the file has no video or no audio streams.
        """
        if not self.video_streams:
            raise VideoCleanError(path=self.path, reason="no video streams found")
        if not self.audio_streams:
            raise VideoCleanError(path=self.path, reason="no audio streams found")

        plan = self._build_plan(config=config, options=options)
        self._note_unusable_language_tags(plan)

        needs_reorder = self._need_stream_reorder()
        plan.actions.insert(
            0,
            PlanAction(
                label="Reorder streams",
                applied=needs_reorder,
                reason=None if needs_reorder else "streams already in order",
            ),
        )
        return plan

    def plan_changes_file(self, plan: ConversionPlan) -> bool:
        """Check whether running a plan would change the file, so a no-op can skip ffmpeg.

        Args:
            plan (ConversionPlan): A plan from `plan_clean`.

        Returns:
            bool: True when the plan maps, reorders, or encodes anything differently.
        """
        return not plan.is_noop(stream_count=len(self.all_streams)) or self._need_stream_reorder()

    def _language_for(self, media_id: MediaId, config: Config, notes: list[str]) -> Lang | None:
        """Resolve a single media ID to the content's original language.

        Args:
            media_id (MediaId): The identifier to look up.
            config (Config): The settings holding the TMDB API key.
            notes (list[str]): Debug notes that receive an unusable language code.

        Returns:
            Lang | None: The original language, or None when it cannot be resolved.
        """
        if media_id.source == "imdb":
            response = query_tmdb(media_id.value, api_key=config.tmdb_api_key)
            results = response.get("movie_results") or response.get("tv_results") or []
            original_language = results[0].get("original_language") if results else None
        else:
            response = query_tmdb_by_id(
                tmdb_id=media_id.value,
                media_type=media_id.media_type,
                api_key=config.tmdb_api_key,
            )
            original_language = response.get("original_language")

        if not original_language:
            return None

        # TMDB uses 'cn' for Chinese but iso639 expects 'zh'
        if original_language == "cn":
            original_language = "zh"

        try:
            return Lang(original_language)
        except (InvalidLanguageValue, DeprecatedLanguageValue):
            notes.append(f"iso639: Unusable language '{original_language}' for: {self.name}")
            return None

    def _find_original_language(self, config: Config, *, notes: list[str]) -> Lang | None:
        """Find the original language of the video content.

        Check every ID discoverable from the filename and container tags, then fall back
        to a Radarr/Sonarr title search. Cache the outcome so a miss is not retried.

        Args:
            config (Config): The settings holding the API credentials.
            notes (list[str]): Debug notes that receive why a lookup failed. Only the
                first, uncached call adds to them.

        Returns:
            Lang | None: The determined language, or None when none could be found.
        """
        if self.ran_language_check:
            return self.language

        # Cache the attempt itself, so a miss does not re-query every API on each call
        self.ran_language_check = True

        media_ids = find_media_ids(stem=self.stem, format_tags=self.probe_box.format.tags)

        for media_id in media_ids:
            if language := self._language_for(media_id=media_id, config=config, notes=notes):
                self.language = language
                return language

        # Last resort: Radarr/Sonarr require a network round trip, so only ask
        # once every filename- and container-derived ID has failed to resolve.
        imdb_id = self._query_arr_apps_for_imdb_id(config)

        # Skip IDs already tried above; every one of them just failed to resolve
        tried_imdb_ids = {media_id.value for media_id in media_ids if media_id.source == "imdb"}

        if (
            imdb_id
            and imdb_id not in tried_imdb_ids
            and (
                language := self._language_for(
                    media_id=MediaId(source="imdb", value=imdb_id), config=config, notes=notes
                )
            )
        ):
            self.language = language
            return language

        notes.append(f"Could not find original language for: {self.name}")
        return None

    def _need_stream_reorder(self) -> bool:
        """Check if the video file needs stream reordering.

        Returns:
            bool: True if the video file needs stream reordering, False otherwise.
        """
        return any(
            stream.index != i
            for i, stream in enumerate(
                self.video_streams + self.audio_streams + self.subtitle_streams
            )
        )

    def _query_arr_apps_for_imdb_id(self, config: Config) -> str | None:
        """Query Radarr and Sonarr APIs to find the IMDb ID of the video.

        This method attempts to retrieve the IMDb ID based on the video file's name by utilizing external APIs for Radarr and Sonarr as sources. It first queries Radarr API and checks if the response contains the movie information with the IMDb ID. If found, it returns the IMDb ID.

        If not found, it then queries Sonarr API and checks if the response contains the series information with the IMDb ID. If found, it returns the IMDb ID. If no IMDb ID is found from either API, it returns None.

        Args:
            config (Config): The settings holding the Radarr and Sonarr credentials.

        Returns:
            str | None: The IMDb ID if found, otherwise None.
        """
        radarr = query_radarr(self.name, url=config.radarr_url, api_key=config.radarr_api_key)
        if imdb_id := self._imdb_id_from_arr_response(response=radarr, media_key="movie"):
            return imdb_id

        sonarr = query_sonarr(self.name, url=config.sonarr_url, api_key=config.sonarr_api_key)
        return self._imdb_id_from_arr_response(response=sonarr, media_key="series")

    @staticmethod
    def _imdb_id_from_arr_response(response: dict[str, Any], media_key: str) -> str | None:
        """Read the IMDb ID from a Radarr or Sonarr parse response.

        An unmatched title can come back with the media object null or missing, and a match
        can lack an IMDb ID, so every level is checked before reading it.

        Args:
            response (dict[str, Any]): The parse response body.
            media_key (str): The key holding the matched media, `movie` or `series`.

        Returns:
            str | None: The IMDb ID, or None when the response has none.
        """
        media = response.get(media_key) if isinstance(response, dict) else None
        imdb_id = media.get("imdbId") if isinstance(media, dict) else None
        return imdb_id if isinstance(imdb_id, str) and imdb_id else None

    def prepare_ffmpeg(
        self,
        arguments: list[str],
        *,
        suffix: str | None = None,
        step: str | None = None,
        config: Config,
        dryrun: bool,
    ) -> FfmpegCommand:
        """Build the full ffmpeg command for one processing step on this file.

        Read from the latest temporary file so steps chain, and write to the next one. A dry
        run gets a preview output path, so building its command touches nothing on disk.

        Args:
            arguments (list[str]): The step's ffmpeg arguments, placed between the input
                and the output.
            suffix (str | None, optional): Suffix for the output file. Use when creating a new container mime type. Defaults to None.
            step (str | None, optional): Step name for file naming. Defaults to None.
            config (Config): The settings for this run, which name the cache directory.
            dryrun (bool): Preview the command instead of preparing to run it.

        Returns:
            FfmpegCommand: The command and the temporary file it writes.
        """
        input_path = self.temp_file.latest_temp_path()
        make_path = self.temp_file.preview_tmp_path if dryrun else self.temp_file.new_tmp_path
        output_path = make_path(suffix=suffix, step_name=step, cache_dir=config.cache_dir)

        # Prepend global ffmpeg options before input file to ensure consistent behavior
        command = [
            "ffmpeg",
            *FFMPEG_PREPEND,
            "-i",
            str(input_path),
            *arguments,
            *FFMPEG_APPEND,
            str(output_path),
        ]
        return FfmpegCommand(arguments=command, output_path=output_path)

    def run_ffmpeg(self, command: FfmpegCommand, *, on_progress: Callable[[float], None]) -> None:
        """Run a prepared ffmpeg command and make its output the file's latest version.

        Args:
            command (FfmpegCommand): A command from `prepare_ffmpeg` with `dryrun` unset.
            on_progress (Callable[[float], None]): Receives the percent complete as ffmpeg
                reports it, so the caller can show progress.

        Raises:
            cappa.Exit: If the user interrupts ffmpeg, after removing temporary files.
        """
        ff = FfmpegProgress(command.arguments)

        try:
            for complete in ff.run_command_with_progress():
                on_progress(complete)
        except KeyboardInterrupt as e:
            # Clean up temporary files if user interrupts to avoid orphaned files
            self.temp_file.clean_up()
            message = "Interrupted while running ffmpeg. Exiting..."
            raise cappa.Exit(message, code=1) from e

        self.temp_file.created_temp_file(command.output_path)

    def clip_arguments(self, start: str, duration: str) -> list[str]:
        """Build the ffmpeg arguments that copy a segment of the video without re-encoding.

        Args:
            start (str): Start time of the clip.
            duration (str): Duration of the clip.

        Returns:
            list[str]: Arguments for `prepare_ffmpeg`.

        Raises:
            VideoCleanError: If the file has no video streams.
        """
        if not self.video_streams:
            raise VideoCleanError(path=self.path, reason="no video streams found")

        return ["-ss", start, "-t", duration, "-map", "0", "-c", "copy"]

    def ffprobe_json(self) -> dict[str, Any]:
        """Run ffprobe on the video file and return the JSON response.

        Returns:
            dict: A dictionary containing the ffprobe output with information about the video file's streams, format, and metadata.
        """
        return run_ffprobe(self.path)
