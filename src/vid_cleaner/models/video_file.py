"""VideoFile model."""

import math
import re
from pathlib import Path
from typing import Any

from box import Box
from iso639 import Lang
from iso639.exceptions import DeprecatedLanguageValue, InvalidLanguageValue

from vid_cleaner.config import Config, RunOptions
from vid_cleaner.constants import (
    ALL_AUDIO_KEPT_REASON,
    ALREADY_MODERN_CODEC_REASON,
    COMMENTARY_STREAM_TITLE_REGEX,
    CONVERT_TO_H265_LABEL,
    CONVERT_TO_VP9_LABEL,
    DEINTERLACE_LABEL,
    DEINTERLACE_LABEL_TEMPLATE,
    DOWNMIX_STEREO_FILTER,
    DOWNMIX_STEREO_LABEL,
    DROP_AUDIO_LABEL,
    DROP_SUBTITLES_LABEL,
    EXCLUDED_VIDEO_CODECS,
    FHD_RESOLUTION,
    H265_CODECS,
    HDTV_RESOLUTION,
    INTERLACE_QUESTION_NOTICE,
    INTERLACE_UNDECIDED_REASON,
    KEEP_ALL_SUBTITLES_REASON,
    MIN_SURROUND_CHANNELS,
    NO_AUDIO_STREAMS_REASON,
    NO_SUBTITLES_REASON,
    NO_SURROUND_SOURCE_REASON,
    NO_UNWANTED_SUBTITLES_REASON,
    NO_VIDEO_STREAMS_REASON,
    NOT_INTERLACED_REASON,
    REORDER_STREAMS_LABEL,
    SCALE_TO_1080P_LABEL,
    SDTV_RESOLUTION,
    SILENT_FILE_REASON,
    STEREO_EXISTS_REASON,
    STREAMS_IN_ORDER_REASON,
    TEXT_SUBTITLE_CODECS,
    UHDTV_RESOLUTION,
    UNKNOWN_DURATION_REASON,
    AudioLayout,
    CodecTypes,
    Resolution,
    VideoTrait,
)
from vid_cleaner.exceptions import VideoCleanError
from vid_cleaner.models.conversion_plan import ConversionPlan, OutputStream, PlanAction
from vid_cleaner.utils import (
    FfmpegCommand,
    InterlaceScan,
    MediaId,
    ProbeCache,
    build_ffmpeg_command,
    find_media_ids,
    get_probe_as_box,
    radarr_imdb_id,
    scan_with_cache,
    sonarr_imdb_id,
    tmdb_original_language,
)

from vid_cleaner.controllers import TempFile  # isort: skip

_LAYOUT_TRAITS: dict[AudioLayout, VideoTrait] = {
    AudioLayout.STEREO: VideoTrait.STEREO,
    AudioLayout.MONO: VideoTrait.MONO,
    AudioLayout.SURROUND5: VideoTrait.SURROUND5,
    AudioLayout.SURROUND7: VideoTrait.SURROUND7,
}

# Checked in order; the first resolution whose height or width matches wins
_RESOLUTION_TRAITS: tuple[tuple[Resolution, VideoTrait], ...] = (
    (HDTV_RESOLUTION, VideoTrait.HDTV),
    (FHD_RESOLUTION, VideoTrait.FHD),
    (UHDTV_RESOLUTION, VideoTrait.UHDTV),
    (SDTV_RESOLUTION, VideoTrait.SDTV),
)


def _parse_language(code: str) -> Lang | None:
    """Parse an ISO 639 code, treating one that iso639 rejects as unknown.

    Args:
        code (str): The language code to parse.

    Returns:
        Lang | None: The language, or None when the code is not a usable ISO 639 code.
    """
    try:
        return Lang(code)
    except (InvalidLanguageValue, DeprecatedLanguageValue):
        return None


class VideoFile:
    """VideoFile model."""

    def __init__(self, path: Path) -> None:
        """Initialize VideoFile."""
        self.path = path.expanduser().resolve()
        self.name = path.name
        self.stem = path.stem
        self.temp_file = TempFile(self.path)

        self.language: Lang | None = None
        self.ran_language_check = False
        self._probe_box: Box = Box({}, default_box=True, default_box_create_on_get=False)
        self._interlace_scan: InterlaceScan | None = None

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

    def load_probe(self, probe_cache: ProbeCache) -> None:
        """Probe the source file through a cache of earlier results, ahead of first use.

        Call this before reading `probe_box` when probing many files, so an unchanged file
        reuses its stored ffprobe output. The cache never sees temporary files, which
        `probe_box` probes directly once a run has written one.

        Args:
            probe_cache (ProbeCache): The cache to read and fill.
        """
        if self.temp_file.latest_temp_path() == self.path:
            self._probe_box = get_probe_as_box(self.path, probe_cache=probe_cache)

    def interlace_scan(self, probe_cache: ProbeCache | None = None) -> InterlaceScan:
        """Scan the source file for interlacing, once per instance.

        Call this when a plan needs to know whether to deinterlace or warn. The scan decodes
        part of the video, so the verdict is memoized and, given a cache, stored for the
        next run. It always reads the source file, never a temporary output.

        Args:
            probe_cache (ProbeCache | None): The cache to read and fill. Skipped when None.

        Returns:
            InterlaceScan: The verdict, undecided when the file has too few frames to judge.
        """
        if self._interlace_scan is None:
            self._interlace_scan = scan_with_cache(
                self.path, read_duration=self._source_duration_seconds, probe_cache=probe_cache
            )
        return self._interlace_scan

    def _source_duration_seconds(self) -> float | None:
        """Read the source file's duration, which the interlace scan samples across.

        Returns:
            float | None: The duration in seconds, or None when unknown.
        """
        # `probe_box` follows the latest temporary output, which can be shorter than the source
        source_probe = (
            self.probe_box
            if self.temp_file.latest_temp_path() == self.path
            else get_probe_as_box(self.path)
        )
        return self._known_duration_seconds(source_probe)

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
        traits = [*self._get_audio_traits(), *self._get_video_traits()]
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
        traits: list[VideoTrait] = []
        for stream in self.audio_streams:
            if self._is_commentary_stream(stream):
                traits.append(VideoTrait.COMMENTARY)
            elif trait := _LAYOUT_TRAITS.get(stream.channels):
                traits.append(trait)
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
        traits: list[VideoTrait] = []
        for stream in self.video_streams:
            if stream.codec_name.lower() in H265_CODECS:
                traits.append(VideoTrait.H265)
            elif stream.codec_name.lower() == "h264":
                traits.append(VideoTrait.H264)

            traits.append(
                next(
                    (
                        trait
                        for resolution, trait in _RESOLUTION_TRAITS
                        if stream.height == resolution.height or stream.width == resolution.width
                    ),
                    VideoTrait.UNKNOWN_RESOLUTION,
                )
            )
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
    def _has_undefined_language(stream: Box) -> bool:
        """Check whether a stream carries no language tag or the `und` (undetermined) tag.

        Args:
            stream (Box): The stream to inspect.

        Returns:
            bool: True when the stream names no language.
        """
        return not stream.language or str(stream.language).lower() == "und"

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
        if VideoFile._has_undefined_language(stream):
            return None
        # `_note_unusable_language_tags` reports unusable tags in the plan's debug notes
        return _parse_language(str(stream.language).lower())

    def _note_unusable_language_tags(self, plan: ConversionPlan) -> None:
        """Record each audio or subtitle language tag that planning treats as unknown.

        Args:
            plan (ConversionPlan): The plan whose debug notes receive the findings.
        """
        plan.debug_notes.extend(
            f"Stream #{stream.index}: unusable language tag '{stream.language}', treated as unknown"
            for stream in self.audio_streams + self.subtitle_streams
            if not self._has_undefined_language(stream) and self._stream_language(stream) is None
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

    @staticmethod
    def _is_stereo_mix(stream: Box) -> bool:
        """Check whether a stream is a stereo mix of the main audio.

        Commentary tracks are often stereo but are not a mix of the main audio, so they never
        count as one.

        Args:
            stream (Box): The stream to inspect.

        Returns:
            bool: True if the stream is a non-commentary stereo track.
        """
        return stream.channels == AudioLayout.STEREO and not VideoFile._is_commentary_stream(stream)

    def _needs_stereo(self) -> bool:
        """Check whether the file lacks a stereo mix but could have one built from its audio.

        Distinguishes a file worth downmixing from one that merely has no stereo track, such as
        an original mono recording, which has nothing to downmix and should be left alone.

        Returns:
            bool: True if no non-commentary stereo mix exists and a surround bed does.
        """
        has_stereo_mix = any(self._is_stereo_mix(stream) for stream in self.audio_streams)
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
    ) -> tuple[list[OutputStream], list[Box], PlanAction]:
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
            tuple[list[OutputStream], list[Box], PlanAction]: The planned downmix output
                streams, the existing streams the caller must not map, and the recorded
                action.
        """
        existing_stereo: list[Box] = [
            stream for stream in streams if VideoFile._is_stereo_mix(stream)
        ]
        if existing_stereo and not force:
            skip = PlanAction(
                label=DOWNMIX_STEREO_LABEL, applied=False, reason=STEREO_EXISTS_REASON
            )
            return [], [], skip

        # One stereo track per file: rank every surround bed and keep the best. `min` returns
        # the first of equal ranks, so file order breaks ties.
        surround_source = min(
            (s for s in streams if VideoFile._is_surround_stream(s)),
            key=lambda s: VideoFile._downmix_source_rank(stream=s, langs=langs),
            default=None,
        )
        if surround_source is None:
            skip = PlanAction(
                label=DOWNMIX_STEREO_LABEL, applied=False, reason=NO_SURROUND_SOURCE_REASON
            )
            return [], [], skip

        # Forced recreation replaces the existing stereo mix in the surround bed's language
        source_language = VideoFile._stream_language(surround_source)
        streams_to_drop: list[Box] = [
            stream
            for stream in existing_stereo
            if VideoFile._stream_language(stream) == source_language
        ]
        downmix = OutputStream(
            source_index=surround_source.index,
            codec_type=CodecTypes.AUDIO,
            codec="aac",
            stream_filter=DOWNMIX_STEREO_FILTER,
            extra_args=["-ac:a:{n}", "2", "-b:a:{n}", "256k", "-ar:a:{n}", "48000"],
            metadata={"title": "2.0"},
        )
        return [downmix], streams_to_drop, PlanAction(label=DOWNMIX_STEREO_LABEL, applied=True)

    def _plan_audio_streams(
        self,
        plan: ConversionPlan,
        config: Config,
        options: RunOptions,
        *,
        langs: list[Lang],
    ) -> list[OutputStream]:
        """Plan audio streams to keep, honoring language, commentary, and downmix settings.

        Args:
            plan (ConversionPlan): The plan whose actions record drop-audio and downmix
                outcomes.
            config (Config): The settings for this run.
            options (RunOptions): The CLI-only values for this run.
            langs (list[Lang]): Audio languages to keep, in preference order.

        Returns:
            list[OutputStream]: Copy-mapped kept audio followed by any planned downmix.
        """
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

        if not streams_to_keep:
            # Removing every stream would leave a silent video
            streams_to_keep = list(self.audio_streams)
            plan.actions.append(
                PlanAction(
                    label=DROP_AUDIO_LABEL,
                    applied=False,
                    reason=SILENT_FILE_REASON,
                )
            )
        else:
            dropped = len(self.audio_streams) - len(streams_to_keep)
            plan.actions.append(
                PlanAction(
                    label=DROP_AUDIO_LABEL,
                    applied=dropped > 0,
                    reason=None if dropped > 0 else ALL_AUDIO_KEPT_REASON,
                )
            )

        downmix_streams: list[OutputStream] = []
        streams_to_drop: list[Box] = []
        if config.downmix_stereo:
            # Forced recreation can request dropping an existing stereo track
            downmix_streams, streams_to_drop, downmix_action = self._plan_downmix(
                streams=streams_to_keep, langs=langs, force=options.force
            )
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

        # `und` streams count as a match so important subtitles are not removed
        language = VideoFile._stream_language(stream)
        matches_keep_languages = VideoFile._has_undefined_language(stream) or (
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

    def _plan_subtitle_streams(
        self,
        plan: ConversionPlan,
        config: Config,
        *,
        langs: list[Lang],
        original_language: Lang | None,
    ) -> list[OutputStream]:
        """Plan subtitle streams to keep based on language preferences.

        Args:
            plan (ConversionPlan): The plan whose actions record the drop-subtitles
                outcome.
            config (Config): The settings for this run.
            langs (list[Lang]): The configured keep languages.
            original_language (Lang | None): The file's original audio language, or None
                when unknown or not looked up.

        Returns:
            list[OutputStream]: Copy-mapped kept subtitle streams.
        """
        keep: list[OutputStream] = []

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
            reason = NO_SUBTITLES_REASON
        elif config.keep_all_subtitles:
            reason = KEEP_ALL_SUBTITLES_REASON
        else:
            reason = NO_UNWANTED_SUBTITLES_REASON
        plan.actions.append(
            PlanAction(label=DROP_SUBTITLES_LABEL, applied=dropped > 0, reason=reason)
        )

        return keep

    def _is_modern_codec_source(self, options: RunOptions) -> bool:
        """Check whether the source is already H.265/VP9 and `force` does not override that.

        Args:
            options (RunOptions): The CLI-only values for this run.

        Returns:
            bool: True when a codec conversion has nothing to change, ignoring deinterlacing.
        """
        return not options.force and self.video_streams[0].codec_name.lower() in H265_CODECS

    def _known_duration_seconds(self, probe: Box) -> float | None:
        """Read a usable duration, which H.265 bitrate targets and scan sampling depend on.

        Prefer the container duration, then the first video stream's.

        Args:
            probe (Box): The probe of the file whose duration is wanted.

        Returns:
            float | None: A finite, positive duration in seconds, or None when neither is usable.
        """
        stream_duration = self.video_streams[0].duration if self.video_streams else None
        for value in (probe.duration, stream_duration):
            try:
                seconds = float(value or 0)
            except (TypeError, ValueError):
                continue
            if math.isfinite(seconds) and seconds > 0:
                return seconds
        return None

    def _is_scale_unneeded(self, options: RunOptions) -> bool:
        """Check whether the source is 1080p or smaller and `force` does not override that.

        Args:
            options (RunOptions): The CLI-only values for this run; `force` overrides the check.

        Returns:
            bool: True when the 1080p scale has nothing to shrink.
        """
        source_width = self.video_streams[0].width or 0
        return source_width <= FHD_RESOLUTION.width and not options.force

    def will_reencode_video(self, options: RunOptions) -> bool:
        """Check whether the requested codec or scale work re-encodes the video.

        Call this to decide whether a deinterlace question is worth asking. It reflects
        what the codec and scale planners decide and never scans for interlacing.

        Args:
            options (RunOptions): The CLI-only values for this run.

        Returns:
            bool: True when `--h265`, `--vp9`, or `--1080p` changes the video for this file.
        """
        if not self.video_streams:
            return False
        modern = self._is_modern_codec_source(options)
        return (
            (
                options.h265
                and not modern
                and self._known_duration_seconds(self.probe_box) is not None
            )
            or (options.vp9 and not modern)
            or (options.video_1080 and not self._is_scale_unneeded(options))
        )

    def _skip_codec_conversion(self, plan: ConversionPlan, label: str, options: RunOptions) -> bool:
        """Record a skipped codec conversion when the video already uses a modern codec.

        Deinterlacing an interlaced modern-codec source needs an encode, so it lifts the
        skip the same way `force` does.

        Args:
            plan (ConversionPlan): The plan that receives the skipped action.
            label (str): The conversion's action label.
            options (RunOptions): The CLI-only values for this run; `force` disables the skip.

        Returns:
            bool: True when the conversion was skipped and the caller should stop planning it.
        """
        if not self._is_modern_codec_source(options):
            return False
        if options.deinterlace is True and self.interlace_scan().interlaced:
            return False
        plan.actions.append(
            PlanAction(label=label, applied=False, reason=ALREADY_MODERN_CODEC_REASON)
        )
        return True

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
        if self._skip_codec_conversion(plan=plan, label=CONVERT_TO_H265_LABEL, options=options):
            return

        video_stream = self.video_streams[0]
        # Calculate target bitrate using Frame.io's formula: https://blog.frame.io/2017/03/06/calculate-video-bitrates/
        # This formula provides good quality while maintaining reasonable file sizes
        stream_duration = self._known_duration_seconds(self.probe_box)
        if stream_duration is None:
            plan.actions.append(
                PlanAction(
                    label=CONVERT_TO_H265_LABEL,
                    applied=False,
                    reason=UNKNOWN_DURATION_REASON,
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
        width = video_stream.width or 0
        target_width = FHD_RESOLUTION.width
        scale_factor = (
            (target_width / width) ** 2 if options.video_1080 and width > target_width else 1.0
        )

        # Calculate bitrates with a target of 50% of original size while maintaining quality
        current_bitrate = int(file_size_megabytes / (duration * 0.0075) * scale_factor)
        target_bitrate = int(current_bitrate / 2)
        # Allow 30% variance from target bitrate to handle complex scenes
        min_bitrate = int(current_bitrate * 0.7)
        max_bitrate = int(current_bitrate * 1.3)

        for video_output in plan.streams_of(CodecTypes.VIDEO):
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
        plan.actions.append(PlanAction(label=CONVERT_TO_H265_LABEL, applied=True))

    def _plan_vp9(self, plan: ConversionPlan, options: RunOptions) -> None:
        """Plan a VP9/WebM conversion, adapting audio and subtitles to the container.

        WebM only carries Vorbis/Opus audio and WebVTT subtitles, so every kept audio
        stream (including a planned downmix) targets libvorbis, text subtitles convert
        to WebVTT, and image-based subtitles are dropped with a notice.

        Args:
            plan (ConversionPlan): The plan mutated in place.
            options (RunOptions): The CLI-only values for this run.
        """
        if self._skip_codec_conversion(plan=plan, label=CONVERT_TO_VP9_LABEL, options=options):
            return

        destination = options.out or self.path
        if destination.suffix != ".webm":
            plan.notices.append(
                f"Converting to VP9, setting output to `{destination.with_suffix('.webm').name}`"
            )
        plan.output_suffix = ".webm"
        # Data streams and chapters may corrupt WebM output
        plan.global_args.extend(["-dn", "-map_chapters", "-1"])

        # WebM has no attachment streams, so fonts and other attachments cannot be kept
        plan.keep_attachments = False
        plan.notices.extend(
            f"Dropping attachment stream 0:{stream.index}; WebM cannot carry attachments"
            for stream in self.probe_box.streams
            if stream.codec_type == CodecTypes.ATTACHMENT
        )

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
        plan.actions.append(PlanAction(label=CONVERT_TO_VP9_LABEL, applied=True))

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

    def _plan_deinterlace(self, plan: ConversionPlan, options: RunOptions) -> None:
        """Plan a bwdif deinterlace of every kept video stream when the source is interlaced.

        With the choice unanswered (`None`), only a dry run reaches here with an interlaced
        file; it gets a notice instead of a filter. The scan runs only when the video is
        re-encoded anyway.

        Args:
            plan (ConversionPlan): The plan whose video streams are annotated in place.
            options (RunOptions): The CLI-only values for this run.
        """
        if options.deinterlace is None:
            if not self.will_reencode_video(options):
                return
            if self.interlace_scan().interlaced:
                plan.notices.append(INTERLACE_QUESTION_NOTICE)
            return
        if options.deinterlace is False:
            return

        scan = self.interlace_scan()
        # An InterlaceScan carries a field order exactly when it found interlacing
        if scan.field_order is None:
            plan.actions.append(
                PlanAction(
                    label=DEINTERLACE_LABEL,
                    applied=False,
                    reason=INTERLACE_UNDECIDED_REASON
                    if scan.interlaced is None
                    else NOT_INTERLACED_REASON,
                )
            )
            return

        deinterlace_filter = f"bwdif=mode=send_frame:parity={scan.field_order.value}:deint=all"
        for video_output in plan.streams_of(CodecTypes.VIDEO):
            video_output.add_filter(deinterlace_filter, first=True)
        plan.actions.append(
            PlanAction(
                label=DEINTERLACE_LABEL_TEMPLATE.format(field_order=scan.field_order.description),
                applied=True,
            )
        )

    def _plan_scale_to_1080p(self, plan: ConversionPlan, options: RunOptions) -> None:
        """Plan scaling the video stream to 1080p.

        A source 1080p or smaller is left alone unless forced. A forced source narrower
        than 1080p is upscaled, and the plan records its width so the CLI can ask first.

        Args:
            plan (ConversionPlan): The plan whose video stream is annotated in place.
            options (RunOptions): The CLI-only values for this run.
        """
        if self._is_scale_unneeded(options):
            plan.actions.append(
                PlanAction(
                    label=SCALE_TO_1080P_LABEL, applied=False, reason="source already ≤1080p"
                )
            )
            return

        source_width = self.video_streams[0].width or 0
        if 0 < source_width < FHD_RESOLUTION.width:
            plan.upscale_source_width = source_width

        # Height -2 keeps the aspect ratio while forcing the even dimension encoders need
        scale_filter = f"scale=width={FHD_RESOLUTION.width}:height=-2"
        for video_output in plan.streams_of(CodecTypes.VIDEO):
            video_output.add_filter(scale_filter)
        plan.actions.append(PlanAction(label=SCALE_TO_1080P_LABEL, applied=True))

    def _build_plan(self, config: Config, options: RunOptions) -> ConversionPlan:
        """Compose every requested operation into a single-pass conversion plan.

        The codec and scale planners read the first video stream, so the file must have one;
        `plan_clean` checks before calling this.

        Args:
            config (Config): The settings for this run.
            options (RunOptions): The CLI-only values for this run.

        Returns:
            ConversionPlan: The composed plan, ready to build one ffmpeg command.
        """
        plan = ConversionPlan()
        needs_reorder = self._need_stream_reorder()
        plan.actions.append(
            PlanAction(
                label=REORDER_STREAMS_LABEL,
                applied=needs_reorder,
                reason=None if needs_reorder else STREAMS_IN_ORDER_REASON,
            )
        )

        keep_langs: list[Lang] = [Lang(lang) for lang in config.langs_to_keep]
        # Only look up the original language when a setting uses it, to avoid API calls
        original_language = (
            None
            if config.drop_original_audio and config.drop_local_subs
            else self._find_original_language(config=config, notes=plan.debug_notes)
        )
        audio_langs: list[Lang] = list(keep_langs)
        if (
            not config.drop_original_audio
            and original_language
            and original_language not in audio_langs
        ):
            audio_langs.append(original_language)

        video = self._plan_video_streams()
        audio = self._plan_audio_streams(
            plan=plan, config=config, options=options, langs=audio_langs
        )
        subtitles = self._plan_subtitle_streams(
            plan=plan,
            config=config,
            langs=keep_langs,
            original_language=None if config.drop_local_subs else original_language,
        )
        plan.streams = video + audio + subtitles

        # Codec planners run first so later filters only fall back to the container default
        # encoder when no explicit codec was requested. Deinterlacing precedes scaling so
        # the scale filter sees whole frames.
        if options.h265:
            self._plan_h265(plan=plan, options=options)
        if options.vp9:
            self._plan_vp9(plan=plan, options=options)
        if options.deinterlace is not False:
            self._plan_deinterlace(plan=plan, options=options)
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
            raise VideoCleanError(path=self.path, reason=NO_VIDEO_STREAMS_REASON)
        if not self.audio_streams:
            raise VideoCleanError(path=self.path, reason=NO_AUDIO_STREAMS_REASON)

        plan = self._build_plan(config=config, options=options)
        self._note_unusable_language_tags(plan)
        return plan

    def plan_changes_file(self, plan: ConversionPlan) -> bool:
        """Check whether running a plan would change the file, so a no-op can skip ffmpeg.

        Args:
            plan (ConversionPlan): A plan from `plan_clean`.

        Returns:
            bool: True when the plan maps, reorders, or encodes anything differently.
        """
        # Planners map streams in `all_streams` order, so an identity mapping also means
        # no reorder is needed
        return not plan.is_noop(stream_count=len(self.all_streams))

    def _language_for(self, media_id: MediaId, config: Config, notes: list[str]) -> Lang | None:
        """Resolve a single media ID to the content's original language.

        Args:
            media_id (MediaId): The identifier to look up.
            config (Config): The settings holding the TMDB API key.
            notes (list[str]): Debug notes that receive an unusable language code.

        Returns:
            Lang | None: The original language, or None when it cannot be resolved.
        """
        original_language = tmdb_original_language(media_id=media_id, api_key=config.tmdb_api_key)
        if not original_language:
            return None

        language = _parse_language(original_language)
        if language is None:
            notes.append(f"iso639: Unusable language '{original_language}' for: {self.name}")
        return language

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
        # Cache a miss too, so a failed lookup does not re-query every API on each call
        if not self.ran_language_check:
            self.language = self._look_up_original_language(config=config, notes=notes)
            self.ran_language_check = True
        return self.language

    def _look_up_original_language(self, config: Config, *, notes: list[str]) -> Lang | None:
        """Query the metadata APIs for the original language, without caching.

        Args:
            config (Config): The settings holding the API credentials.
            notes (list[str]): Debug notes that receive why a lookup failed.

        Returns:
            Lang | None: The determined language, or None when none could be found.
        """
        media_ids = find_media_ids(stem=self.stem, format_tags=self.probe_box.format.tags)
        for media_id in media_ids:
            if language := self._language_for(media_id=media_id, config=config, notes=notes):
                return language

        # Last resort: Radarr/Sonarr require a network round trip, so only ask
        # once every filename- and container-derived ID has failed to resolve.
        imdb_id = radarr_imdb_id(
            title=self.name, url=config.radarr_url, api_key=config.radarr_api_key
        ) or sonarr_imdb_id(title=self.name, url=config.sonarr_url, api_key=config.sonarr_api_key)
        tried_imdb_ids = {media_id.value for media_id in media_ids if media_id.source == "imdb"}
        if imdb_id and imdb_id not in tried_imdb_ids:
            arr_media_id = MediaId(source="imdb", value=imdb_id)
            if language := self._language_for(media_id=arr_media_id, config=config, notes=notes):
                return language

        notes.append(f"Could not find original language for: {self.name}")
        return None

    def _need_stream_reorder(self) -> bool:
        """Check if the video file needs stream reordering.

        Returns:
            bool: True if the video file needs stream reordering, False otherwise.
        """
        return any(stream.index != i for i, stream in enumerate(self.all_streams))

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
        make_path = self.temp_file.preview_tmp_path if dryrun else self.temp_file.new_tmp_path
        output_path = make_path(suffix=suffix, step_name=step, cache_dir=config.cache_dir)
        return build_ffmpeg_command(
            arguments=arguments,
            input_path=self.temp_file.latest_temp_path(),
            output_path=output_path,
        )

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
            raise VideoCleanError(path=self.path, reason=NO_VIDEO_STREAMS_REASON)

        return ["-ss", start, "-t", duration, "-map", "0", "-c", "copy"]

    def ffprobe_output(self) -> dict[str, Any]:
        """Return ffprobe's raw output for the file, unchanged, so reports can show exactly what ffprobe said.

        Returns:
            dict[str, Any]: ffprobe's `streams` and `format` data before normalization.
        """
        return self.probe_box.ffprobe_output.to_dict()
