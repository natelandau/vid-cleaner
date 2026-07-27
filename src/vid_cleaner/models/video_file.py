"""VideoFile model."""

import atexit
import re
from pathlib import Path

import cappa
from box import Box
from ffmpeg_progress_yield import FfmpegProgress
from iso639 import Lang
from iso639.exceptions import DeprecatedLanguageValue, InvalidLanguageValue
from nclutils import pp
from rich.markdown import Markdown
from rich.progress import Progress

from vid_cleaner import settings
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
    SDTV_RESOLUTION,
    SYMBOL_CHECK,
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
    render_operations,
    run_ffprobe,
)

from vid_cleaner.controllers import TempFile  # isort: skip


def cleanup_on_exit(video_file: "VideoFile") -> None:  # pragma: no cover
    """Cleanup temporary files on exit.

    Args:
        video_file (VideoFile): The VideoFile object to perform cleanup on.
    """
    video_file.temp_file.clean_up()


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

        atexit.register(cleanup_on_exit, self)

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
        if VideoTrait.STEREO not in traits:
            traits.append(VideoTrait.NOSTEREO)

        if VideoTrait.STEREO not in traits and VideoTrait.MONO not in traits:
            traits.append(VideoTrait.SURROUND_ONLY)

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
    def _plan_downmix(
        streams: list[Box],
    ) -> tuple[list[OutputStream], list[Box], PlanAction | None]:
        """Plan a downmix of the simplest surround bed to a dialogue-forward stereo track.

        Skip the work when a non-commentary stereo mix already exists, unless
        `settings.force` is set, in which case the existing stereo track(s) are dropped
        and rebuilt from the surround bed. Notify the user when downmix is skipped or
        cannot be recreated.

        Args:
            streams (list[Box]): Audio streams that would otherwise be kept.

        Returns:
            tuple[list[OutputStream], list[Box], PlanAction | None]: The planned downmix
                output streams, the existing streams the caller must not map, and the
                recorded action (always populated; callers only invoke this method when
                `settings.downmix_stereo` is set).
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

        # Group surround sources by layout tier and downmix the simplest bed present:
        # 5.1 (5-6ch) over 7.1 (7-8ch) over Atmos (>8ch). A single dialogue-forward filter
        # serves every tier, so a >7.1 track no longer passes through un-downmixed.
        surround5 = [s for s in streams if s.channel_count in (5, 6)]
        surround7 = [s for s in streams if s.channel_count in (7, 8)]
        surround_gt7 = [
            s for s in streams if s.channel_count and s.channel_count > AudioLayout.SURROUND7.value
        ]
        surround_source = surround5 or surround7 or surround_gt7

        if existing_stereo:
            if not settings.force:
                pp.info(
                    "Stereo track already exists; skipping downmix. Use --force to recreate it."
                )
                action = PlanAction(
                    label="Downmix to stereo",
                    applied=False,
                    reason="stereo track already exists; use --force",
                )
                return downmix_streams, streams_to_drop, action
            if not surround_source:
                pp.info(
                    "No surround source to recreate stereo from; keeping existing stereo track."
                )
                action = PlanAction(
                    label="Downmix to stereo", applied=False, reason="no surround source to downmix"
                )
                return downmix_streams, streams_to_drop, action
            # Forced recreation: drop the existing stereo mix and rebuild it from the surround bed
            streams_to_drop = existing_stereo

        downmix_streams = [
            OutputStream(
                source_index=stream.index,
                codec_type=CodecTypes.AUDIO,
                codec="aac",
                stream_filter=DOWNMIX_STEREO_FILTER,
                extra_args=["-ac:a:{n}", "2", "-b:a:{n}", "256k", "-ar:a:{n}", "48000"],
                metadata={"title": "2.0"},
            )
            for stream in surround_source
        ]
        action = PlanAction(
            label="Downmix to stereo",
            applied=bool(downmix_streams),
            reason=None if downmix_streams else "no surround source to downmix",
        )
        return downmix_streams, streams_to_drop, action

    def _plan_audio_streams(self, plan: ConversionPlan) -> list[OutputStream]:
        """Plan audio streams to keep, honoring language, commentary, and downmix settings.

        Args:
            plan (ConversionPlan): The plan whose actions record drop-audio and downmix
                outcomes.

        Returns:
            list[OutputStream]: Copy-mapped kept audio followed by any planned downmix.
        """
        langs = [Lang(lang) for lang in settings.langs_to_keep]

        # Add original language to list of languages to keep if not explicitly dropping it
        if not settings.drop_original_audio:
            original_language = self._find_original_language()
            if original_language and original_language not in langs:
                langs.append(original_language)

        streams_to_keep: list[Box] = []
        for stream in self.audio_streams:
            # Unknown language streams are kept to avoid removing potentially important audio
            if not stream.language:
                streams_to_keep.append(stream)
                continue

            # Commentary tracks are often unwanted and take up space
            if not settings.keep_commentary and self._is_commentary_stream(stream):
                pp.trace(rf"PLAN AUDIO: Remove stream #{stream.index} [commentary]")
                continue

            if stream.language == "und" or Lang(stream.language) in langs:
                streams_to_keep.append(stream)
                continue

            pp.trace(f"PLAN AUDIO: Remove stream #{stream.index}")

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
            self._plan_downmix(streams_to_keep) if settings.downmix_stereo else ([], [], None)
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
        stream: Box, langs: list[Lang], original_language: Lang | None
    ) -> bool:
        """Decide whether a single subtitle stream matches the user's keep preferences.

        Args:
            stream (Box): The subtitle stream under evaluation.
            langs (list[Lang]): Languages the user wants to keep.
            original_language (Lang | None): The file's original audio language, or
                None when `settings.drop_local_subs` made it unnecessary to look up.

        Returns:
            bool: True when the stream should be mapped into the output.
        """
        if settings.keep_all_subtitles:
            return True

        if not stream.language:
            return False

        # Keep undefined language streams and streams matching user preferences
        # This ensures we don't accidentally remove important subtitles
        if settings.keep_local_subtitles and (
            stream.language.lower() == "und" or Lang(stream.language) in langs
        ):
            return True

        # Keep subtitles in user's languages when original audio differs
        # This ensures subtitles are available when needed for translation
        return bool(
            not settings.drop_local_subs
            and langs
            and original_language not in langs
            and (stream.language.lower() == "und" or Lang(stream.language) in langs)
        )

    def _plan_subtitle_streams(self, plan: ConversionPlan) -> list[OutputStream]:
        """Plan subtitle streams to keep based on language preferences.

        Args:
            plan (ConversionPlan): The plan whose actions record the drop-subtitles
                outcome.

        Returns:
            list[OutputStream]: Copy-mapped kept subtitle streams.
        """
        keep: list[OutputStream] = []

        langs = [Lang(lang) for lang in settings.langs_to_keep]

        # Only look up original language if we're not explicitly dropping local subs
        # This avoids unnecessary API calls
        original_language = None if settings.drop_local_subs else self._find_original_language()

        # Skip evaluating streams entirely when settings drop every local subtitle;
        # a single return path below still records the drop-subtitles action.
        drop_all_local = (
            not settings.keep_all_subtitles
            and not settings.keep_local_subtitles
            and settings.drop_local_subs
        )

        if not drop_all_local:
            for stream in self.subtitle_streams:
                # Remove commentary/SDH/description tracks unless explicitly kept
                # These are typically supplementary and take up extra space
                if not settings.keep_commentary and self._is_commentary_stream(stream):
                    pp.trace(rf"PLAN SUBTITLES: Remove stream #{stream.index} [commentary]")
                    continue

                if self._should_keep_subtitle(stream, langs, original_language):
                    keep.append(
                        OutputStream(source_index=stream.index, codec_type=CodecTypes.SUBTITLE)
                    )
                    continue

                pp.trace(f"PLAN SUBTITLES: Remove stream #{stream.index}")

        dropped = len(self.subtitle_streams) - len(keep)
        if dropped > 0:
            reason: str | None = None
        elif not self.subtitle_streams:
            reason = "no subtitles to drop"
        elif settings.keep_all_subtitles:
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

    def _plan_h265(self, plan: ConversionPlan) -> None:
        """Plan an H.265 encode of the video stream with size-derived bitrate targets.

        Args:
            plan (ConversionPlan): The plan whose video stream is annotated in place.
        """
        video_stream = self._first_video_stream()
        if not video_stream:
            pp.error("No video stream found")
            plan.actions.append(
                PlanAction(label="Convert to H.265", applied=False, reason="no video stream")
            )
            return

        if not settings.force and video_stream.codec_name.lower() in H265_CODECS:
            pp.warning(
                "H265 ENCODE: Video already H.265 or VP9.",
                details=["Run with `--force` to re-encode.", "Skipping"],
            )
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
            pp.error("Could not calculate video duration")
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
        scale_factor = (1920 / width) ** 2 if settings.video_1080 and width > 1920 else 1.0  # noqa: PLR2004

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

    def _plan_vp9(self, plan: ConversionPlan) -> None:
        """Plan a VP9/WebM conversion, adapting audio and subtitles to the container.

        WebM only carries Vorbis/Opus audio and WebVTT subtitles, so every kept audio
        stream (including a planned downmix) targets libvorbis, text subtitles convert
        to WebVTT, and image-based subtitles are dropped with a notice.

        Args:
            plan (ConversionPlan): The plan mutated in place.
        """
        video_stream = self._first_video_stream()
        if not video_stream:
            pp.error("No video stream found")
            plan.actions.append(
                PlanAction(label="Convert to VP9", applied=False, reason="no video stream")
            )
            return

        # Skip re-encoding if already in modern codec unless forced
        if not settings.force and video_stream.codec_name.lower() in H265_CODECS:
            pp.warning(
                "VP9 ENCODE: Video already H.265 or VP9.",
                details=["Run with `--force` to re-encode.", "Skipping"],
            )
            plan.actions.append(
                PlanAction(
                    label="Convert to VP9", applied=False, reason="already H.265/VP9; use --force"
                )
            )
            return

        if Path(settings.out_path).suffix != ".webm":
            pp.info(
                f"Converting to VP9, setting output to `{settings.out_path.with_suffix('.webm').name}`"
            )
            settings.out_path = settings.out_path.with_suffix(".webm")

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
                    pp.info(
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

    def _plan_scale_to_1080p(self, plan: ConversionPlan) -> None:
        """Plan downscaling the video stream to 1080p.

        Args:
            plan (ConversionPlan): The plan whose video stream is annotated in place.
        """
        video_stream = self._first_video_stream()
        if not video_stream:
            pp.error("No video stream found")
            plan.actions.append(
                PlanAction(label="Convert to 1080p", applied=False, reason="no video stream")
            )
            return

        # Skip downscaling if video is already 1080p or smaller, unless forced
        if not settings.force and (getattr(video_stream, "width", 0) or 0) <= 1920:  # noqa: PLR2004
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

    def _build_plan(self) -> ConversionPlan:
        """Compose every requested operation into a single-pass conversion plan.

        Returns:
            ConversionPlan: The composed plan, ready to build one ffmpeg command.
        """
        plan = ConversionPlan()
        video = self._plan_video_streams()
        audio = self._plan_audio_streams(plan)
        subtitles = self._plan_subtitle_streams(plan)
        plan.streams = video + audio + subtitles

        # Codec planners run before the scale planner so scaling only falls back to the
        # container default encoder when no explicit codec was requested.
        if settings.h265:
            self._plan_h265(plan)
        if settings.vp9:
            self._plan_vp9(plan)
        if settings.video_1080:
            self._plan_scale_to_1080p(plan)

        return plan

    def clean(self) -> list[PlanAction]:
        """Apply every requested cleaning operation in a single ffmpeg pass.

        Compose stream selection, reordering, downmix, scaling, and codec conversion
        into one command so the file is decoded and written exactly once. Skip ffmpeg
        entirely when the plan would change nothing. Report the operations considered,
        up front, before the ffmpeg progress bar starts.

        Returns:
            list[PlanAction]: The plan's operations, applied or skipped, for the
                caller to inspect.

        Raises:
            VideoCleanError: If the file has no video or no audio streams.
        """
        if not self.video_streams:
            raise VideoCleanError(path=self.path, reason="no video streams found")
        if not self.audio_streams:
            raise VideoCleanError(path=self.path, reason="no audio streams found")

        plan = self._build_plan()

        needs_reorder = self._need_stream_reorder()
        plan.actions.insert(
            0,
            PlanAction(
                label="Reorder streams",
                applied=needs_reorder,
                reason=None if needs_reorder else "streams already in order",
            ),
        )

        debug = int(settings.get("verbosity", 0) or 0) >= 1
        render_operations(plan.actions, debug=debug)

        # Nothing to encode: the up-front render already reported "No changes needed".
        if plan.is_noop(stream_count=len(self.all_streams)) and not needs_reorder:
            return plan.actions

        self._run_ffmpeg(
            plan.build_command(), title="Processing", suffix=plan.output_suffix, step="clean"
        )
        return plan.actions

    def _language_for(self, media_id: MediaId) -> Lang | None:
        """Resolve a single media ID to the content's original language.

        Args:
            media_id (MediaId): The identifier to look up.

        Returns:
            Lang | None: The original language, or None when it cannot be resolved.
        """
        if media_id.source == "imdb":
            response = query_tmdb(media_id.value)
            results = response.get("movie_results") or response.get("tv_results") or []
            original_language = results[0].get("original_language") if results else None
        else:
            response = query_tmdb_by_id(tmdb_id=media_id.value, media_type=media_id.media_type)
            original_language = response.get("original_language")

        if not original_language:
            return None

        # TMDB uses 'cn' for Chinese but iso639 expects 'zh'
        if original_language == "cn":
            original_language = "zh"

        try:
            return Lang(original_language)
        except (InvalidLanguageValue, DeprecatedLanguageValue):
            pp.debug(f"iso639: Unusable language '{original_language}' for: {self.name}")
            return None

    def _find_original_language(self) -> Lang | None:
        """Find the original language of the video content.

        Check every ID discoverable from the filename and container tags, then fall back
        to a Radarr/Sonarr title search. Cache the outcome so a miss is not retried.

        Returns:
            Lang | None: The determined language, or None when none could be found.
        """
        if self.ran_language_check:
            return self.language

        # Cache the attempt itself, so a miss does not re-query every API on each call
        self.ran_language_check = True

        media_ids = find_media_ids(stem=self.stem, format_tags=self.probe_box.format.tags)

        for media_id in media_ids:
            if language := self._language_for(media_id):
                self.language = language
                return language

        # Last resort: Radarr/Sonarr require a network round trip, so only ask
        # once every filename- and container-derived ID has failed to resolve.
        imdb_id = self._query_arr_apps_for_imdb_id()

        # Skip IDs already tried above; every one of them just failed to resolve
        tried_imdb_ids = {media_id.value for media_id in media_ids if media_id.source == "imdb"}

        if (
            imdb_id
            and imdb_id not in tried_imdb_ids
            and (language := self._language_for(MediaId(source="imdb", value=imdb_id)))
        ):
            self.language = language
            return language

        pp.debug(f"Could not find original language for: {self.name}")
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

    def _query_arr_apps_for_imdb_id(self) -> str | None:
        """Query Radarr and Sonarr APIs to find the IMDb ID of the video.

        This method attempts to retrieve the IMDb ID based on the video file's name by utilizing external APIs for Radarr and Sonarr as sources. It first queries Radarr API and checks if the response contains the movie information with the IMDb ID. If found, it returns the IMDb ID.

        If not found, it then queries Sonarr API and checks if the response contains the series information with the IMDb ID. If found, it returns the IMDb ID. If no IMDb ID is found from either API, it returns None.

        Returns:
            str | None: The IMDb ID if found, otherwise None.
        """
        response = query_radarr(self.name)
        if response and "movie" in response and "imdbId" in response["movie"]:
            return response["movie"]["imdbId"]

        response = query_sonarr(self.name)
        if response and "series" in response and "imdbId" in response["series"]:
            return response["series"]["imdbId"]

        return None

    def _run_ffmpeg(
        self,
        command: list[str],
        title: str,
        suffix: str | None = None,
        step: str | None = None,
    ) -> list[str]:
        """Execute an ffmpeg command and return its outcome for the caller to display.

        Run the provided ffmpeg command, showing progress and logging information. Determine input and output paths, and manage temporary files related to the operation. Return the substep message describing the result rather than printing it, so the CLI layer owns presentation.

        Args:
            command (list[str]): The ffmpeg command to execute.
            title (str): Title for logging the process.
            suffix (str | None, optional): Suffix for the output file. Use when creating a new container mime type. Defaults to None.
            step (str | None, optional): Step name for file naming. Used when creating a new temporary file. Defaults to None.

        Returns:
            list[str]: Substep messages describing the outcome. Empty on a dry run, since the command is previewed instead of executed.

        Raises:
            cappa.Exit: If KeyboardInterrupt occurs during the ffmpeg command.
        """
        input_path = self.temp_file.latest_temp_path()
        output_path = self.temp_file.new_tmp_path(suffix=suffix, step_name=step)

        # Prepend global ffmpeg options before input file to ensure consistent behavior
        cmd: list[str] = ["ffmpeg", *FFMPEG_PREPEND, "-i", str(input_path)]
        cmd.extend(command)
        cmd.extend([*FFMPEG_APPEND, str(output_path)])

        pp.trace(f"RUN FFMPEG:\n{' '.join(cmd)}")

        if settings.dryrun:
            pp.header(f"{title} (dry run)")
            markdown_command = Markdown(f"```console\n{' '.join(cmd)}\n```")
            pp.console().print(markdown_command)
            return []

        # Use FfmpegProgress to get real-time progress updates during encoding
        ff = FfmpegProgress(cmd)

        try:
            with Progress(transient=True) as progress:
                task = progress.add_task(f"{title}…", total=100)
                for complete in ff.run_command_with_progress():
                    progress.update(task, completed=complete)
        except KeyboardInterrupt as e:
            # Clean up temporary files if user interrupts to avoid orphaned files
            self.temp_file.clean_up()
            pp.warning(f"KeyboardInterrupt during {title.lower()}")
            pp.info("Exiting...")
            raise cappa.Exit(code=1) from e

        self.temp_file.created_temp_file(output_path)
        pp.trace(f"Created temp file: {output_path}")
        return [f"{SYMBOL_CHECK} {title}"]

    def clip(self, start: str, duration: str) -> list[str]:
        """Clip a segment from the video.

        Extract a specific portion of the video based on the given start time and duration. Utilize ffmpeg to perform the clipping operation.

        Args:
            start (str): Start time of the clip.
            duration (str): Duration of the clip.

        Returns:
            list[str]: Substep messages describing the outcome, for the caller to display.
        """
        ffmpeg_command: list[str] = ["-ss", start, "-t", duration, "-map", "0", "-c", "copy"]

        return self._run_ffmpeg(ffmpeg_command, title="Clip video", step="clip")

    def ffprobe_json(self) -> dict:
        """Run ffprobe on the video file and return the JSON response.

        Returns:
            dict: A dictionary containing the ffprobe output with information about the video file's streams, format, and metadata.
        """
        return run_ffprobe(self.path)
