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
    EXCLUDED_VIDEO_CODECS,
    FFMPEG_APPEND,
    FFMPEG_PREPEND,
    FHD_RESOLUTION,
    H265_CODECS,
    HDTV_RESOLUTION,
    SDTV_RESOLUTION,
    SYMBOL_CHECK,
    UHDTV_RESOLUTION,
    AudioLayout,
    CodecTypes,
    VideoTrait,
)
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

    @staticmethod
    def _downmix_to_stereo(streams: list[Box]) -> tuple[list[str], list[Box]]:
        """Plan an ffmpeg downmix of the simplest surround bed to a dialogue-forward stereo track.

        Skip the work when a non-commentary stereo mix already exists, unless `settings.force`
        is set, in which case the existing stereo track(s) are dropped and rebuilt from the
        surround bed. Notify the user when downmix is skipped or cannot be recreated. Return the
        downmix command plus the list of existing audio streams the caller must not map.

        Args:
            streams (list[Box]): Audio streams that would otherwise be kept.

        Returns:
            tuple[list[str], list[Box]]: The downmix ffmpeg fragment and streams to drop.
        """
        downmix_command: list[str] = []
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
                pp.trace(f"PROCESS AUDIO: Downmix command: {downmix_command}")
                return downmix_command, streams_to_drop
            if not surround_source:
                pp.info(
                    "No surround source to recreate stereo from; keeping existing stereo track."
                )
                pp.trace(f"PROCESS AUDIO: Downmix command: {downmix_command}")
                return downmix_command, streams_to_drop
            # Forced recreation: drop the existing stereo mix and rebuild it from the surround bed
            streams_to_drop = existing_stereo

        # The downmix output audio index starts after every mapped audio stream. Excluding
        # dropped streams keeps the per-track options bound to the new downmix, not a kept track.
        base_index = len(streams) - len(streams_to_drop)
        for offset, stream in enumerate(surround_source):
            new_index = base_index + offset
            downmix_command.extend(
                [
                    "-map",
                    f"0:{stream.index}",
                    f"-c:a:{new_index}",
                    "aac",
                    f"-ac:a:{new_index}",
                    "2",
                    f"-b:a:{new_index}",
                    "256k",
                    f"-filter:a:{new_index}",
                    DOWNMIX_STEREO_FILTER,
                    f"-ar:a:{new_index}",
                    "48000",
                    f"-metadata:s:a:{new_index}",
                    "title=2.0",
                ],
            )

        pp.trace(f"PROCESS AUDIO: Downmix command: {downmix_command}")
        return downmix_command, streams_to_drop

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

    def _process_audio(self) -> tuple[list[str], list[str]]:
        """Construct commands for processing audio streams.

        Analyze and process audio streams based on language, commentary, and downmixing criteria. Generate ffmpeg commands for keeping or altering audio streams as required.

        Returns:
            tuple[list[str], list[str]]: A tuple containing two lists of strings forming part of an ffmpeg command for audio processing.
        """
        command: list[str] = []

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
                pp.trace(rf"PROCESS AUDIO: Remove stream #{stream.index} [commentary]")
                continue

            if stream.language == "und" or Lang(stream.language) in langs:
                streams_to_keep.append(stream)
                continue

            pp.trace(f"PROCESS AUDIO: Remove stream #{stream.index}")

        # If every stream would be removed, keep them all to prevent silent video
        if not streams_to_keep:
            streams_to_keep = list(self.audio_streams)

        # Plan the downmix; forced recreation can request dropping an existing stereo track
        downmix_command, streams_to_drop = (
            self._downmix_to_stereo(streams_to_keep) if settings.downmix_stereo else ([], [])
        )

        drop_indices = {stream.index for stream in streams_to_drop}
        for stream in streams_to_keep:
            if stream.index in drop_indices:
                continue
            command.extend(["-map", f"0:{stream.index}"])

        pp.trace(f"PROCESS AUDIO: {command}")
        return command, downmix_command

    def _process_subtitles(self) -> list[str]:
        """Construct a command list for processing subtitle streams.

        Analyze and filter subtitle streams based on language preferences, commentary options, and other criteria. Build an ffmpeg command list accordingly.

        Returns:
            list[str]: A list of strings forming part of an ffmpeg command for subtitle processing.
        """
        command: list[str] = []

        langs = [Lang(lang) for lang in settings.langs_to_keep]

        # Only look up original language if we're not explicitly dropping local subs
        # This avoids unnecessary API calls
        if not settings.drop_local_subs:
            original_language = self._find_original_language()

        # Early return if no subtitle streams should be kept based on settings
        if (
            not settings.keep_all_subtitles
            and not settings.keep_local_subtitles
            and settings.drop_local_subs
        ):
            return command

        for stream in self.subtitle_streams:
            # Remove commentary/SDH/description tracks unless explicitly kept
            # These are typically supplementary and take up extra space
            if not settings.keep_commentary and self._is_commentary_stream(stream):
                pp.trace(rf"PROCESS SUBTITLES: Remove stream #{stream.index} [commentary]")
                continue

            if settings.keep_all_subtitles:
                command.extend(["-map", f"0:{stream.index}"])
                continue

            if stream.language:
                # Keep undefined language streams and streams matching user preferences
                # This ensures we don't accidentally remove important subtitles
                if settings.keep_local_subtitles and (
                    stream.language.lower() == "und" or Lang(stream.language) in langs
                ):
                    pp.trace(f"PROCESS SUBTITLES: Keep stream #{stream.index} (local language)")
                    command.extend(["-map", f"0:{stream.index}"])
                    continue

                # Keep subtitles in user's languages when original audio differs
                # This ensures subtitles are available when needed for translation
                if (
                    not settings.drop_local_subs
                    and langs
                    and original_language not in langs
                    and (stream.language.lower() == "und" or Lang(stream.language) in langs)
                ):
                    pp.trace(f"PROCESS SUBTITLES: Keep stream #{stream.index} (original language)")
                    command.extend(["-map", f"0:{stream.index}"])
                    continue

            pp.trace(f"PROCESS SUBTITLES: Remove stream #{stream.index}")

        pp.trace(f"PROCESS SUBTITLES: {command}")
        return command

    def _process_video(self) -> list[str]:
        """Create a command list for processing video streams.

        Iterate through the provided video streams and construct a list of ffmpeg commands to process them, excluding any streams with codecs in the exclusion list.

        Returns:
            list[str]: A list of strings forming part of an ffmpeg command for video processing.
        """
        command: list[str] = []
        for stream in self.video_streams:
            if stream.codec_name.lower() in EXCLUDED_VIDEO_CODECS:
                continue

            command.extend(["-map", f"0:{stream.index}"])

        pp.trace(f"PROCESS VIDEO: {command}")
        return command

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

    def convert_to_h265(self) -> list[str]:
        """Convert the video to H.265 codec format.

        Check if conversion is necessary and perform it if so. This involves calculating the bitrate, building the ffmpeg command, and running it.

        Returns:
            list[str]: Substep messages describing the outcome, for the caller to display. Empty when conversion is skipped or cannot proceed.
        """
        input_path = self.temp_file.latest_temp_path()

        video_stream = next(
            stream
            for stream in self.probe_box.streams
            if stream.codec_type == CodecTypes.VIDEO
            and stream.codec_name.lower() not in EXCLUDED_VIDEO_CODECS
        )

        if not video_stream:
            pp.error("No video stream found")
            return []

        if not settings.force and video_stream.codec_name.lower() in H265_CODECS:
            pp.warning(
                "H265 ENCODE: Video already H.265 or VP9.",
                details=["Run with `--force` to re-encode.", "Skipping"],
            )
            return []

        # Calculate target bitrate using Frame.io's formula: https://blog.frame.io/2017/03/06/calculate-video-bitrates/
        # This formula provides good quality while maintaining reasonable file sizes
        stream_duration = float(self.probe_box.duration) or float(video_stream.duration)
        if not stream_duration:
            pp.error("Could not calculate video duration")
            return []

        # Convert duration to minutes for bitrate calculation
        duration = stream_duration * 0.0166667

        stat = input_path.stat()
        pp.trace(f"File size: {stat}")
        file_size_megabytes = stat.st_size / 1000000

        # Calculate bitrates with a target of 50% of original size while maintaining quality
        current_bitrate = int(file_size_megabytes / (duration * 0.0075))
        target_bitrate = int(file_size_megabytes / (duration * 0.0075) / 2)
        # Allow 30% variance from target bitrate to handle complex scenes
        min_bitrate = int(current_bitrate * 0.7)
        max_bitrate = int(current_bitrate * 1.3)

        command: list[str] = ["-map", "0", "-c:v", "libx265"]
        command.extend(
            [
                "-b:v",
                f"{target_bitrate}k",
                "-minrate",
                f"{min_bitrate}k",
                "-maxrate",
                f"{max_bitrate}k",
                "-bufsize",
                f"{current_bitrate}k",
            ],
        )

        # Preserve original audio and subtitle streams to maintain quality
        command.extend(["-c:a", "copy", "-c:s", "copy"])

        return self._run_ffmpeg(command, title="Convert to H.265", step="h265")

    def convert_to_vp9(self) -> list[str]:
        """Convert the video to the VP9 codec format.

        Verify if conversion is required and proceed with it using ffmpeg. This method specifically targets the VP9 video codec.

        Returns:
            list[str]: Substep messages describing the outcome, for the caller to display. Empty when conversion is skipped or cannot proceed.
        """
        video_stream = next(
            stream
            for stream in self.probe_box.streams
            if stream.codec_type == CodecTypes.VIDEO
            and stream.codec_name.lower() not in EXCLUDED_VIDEO_CODECS
        )

        if not video_stream:
            pp.error("No video stream found")
            return []

        # Skip re-encoding if already in modern codec unless forced
        if not settings.force and video_stream.codec_name.lower() in H265_CODECS:
            pp.warning(
                "VP9 ENCODE: Video already H.265 or VP9.",
                details=["Run with `--force` to re-encode.", "Skipping"],
            )
            return []

        substeps: list[str] = []
        if Path(settings.out_path).suffix != ".webm":
            substeps.append(
                f"Converting to VP9, setting output to `{settings.out_path.with_suffix('.webm').name}`"
            )
            settings.out_path = settings.out_path.with_suffix(".webm")

        # Use constant quality encoding (CRF) instead of bitrate for better quality control
        command: list[str] = [
            "-map",
            "0",
            "-c:v",
            "libvpx-vp9",
            "-b:v",
            "0",  # Disable fixed bitrate mode
            "-crf",
            "30",  # Higher CRF = lower quality but smaller file size
            "-c:a",
            "libvorbis",  # VP9 typically uses Vorbis audio codec
            "-dn",  # Disable data streams
            "-map_chapters",
            "-1",  # Remove chapters as they may cause issues in WebM
        ]

        command.extend(["-c:s", "copy"])

        substeps.extend(
            self._run_ffmpeg(command, title="Convert to vp9", suffix=".webm", step="vp9")
        )
        return substeps

    def process_streams(self) -> list[str]:
        """Process the video file according to specified audio and subtitle preferences.

        Execute the necessary steps to process the video file, including managing audio and subtitle streams.  Keep or discard audio streams based on specified languages, commentary preferences, and downmix settings. Similarly, filter subtitle streams based on language preferences and criteria such as keeping commentary or local subtitles.

        Returns:
            list[str]: Substep messages describing the outcome, for the caller to display.
        """
        video_map_command = self._process_video()
        audio_map_command, downmix_command = self._process_audio()
        subtitle_map_command = self._process_subtitles()

        title_flags = []

        if audio_map_command:
            title_flags.append("drop original audio") if settings.drop_original_audio else None
            title_flags.append("keep commentary") if settings.keep_commentary else None
            title_flags.append("downmix to stereo") if settings.downmix_stereo else None

        if subtitle_map_command:
            title_flags.append(
                "keep subtitles",
            ) if settings.keep_all_subtitles else title_flags.append("drop unwanted subtitles")
            title_flags.append("keep local subtitles") if settings.keep_local_subtitles else None
            title_flags.append("drop local subtitles") if settings.drop_local_subs else None

        title = f"Process file ({', '.join(title_flags)})" if title_flags else "Process file"

        all_commands = [
            x
            for x in video_map_command + audio_map_command + subtitle_map_command + downmix_command
            if x != "-map"
        ]

        comparison_list = [f"0:{x}" for x in range(len(self.all_streams))]
        if len(comparison_list) == len(all_commands):
            return [f"{SYMBOL_CHECK} No streams to process"]

        return self._run_ffmpeg(
            video_map_command
            + audio_map_command
            + subtitle_map_command
            + ["-c", "copy"]
            + downmix_command,
            title=title,
            step="process",
        )

    def reorder_streams(self) -> list[str]:
        """Reorder the media streams within the video file.

        Arrange the streams in the video file so that video streams appear first, followed by audio streams, and then subtitle streams. Exclude certain types of video streams like 'mjpeg' and 'png'.

        Returns:
            list[str]: Substep messages describing the outcome, for the caller to display.

        Raises:
            cappa.Exit: If no video or audio streams are found in the video file.
        """
        if not self.video_streams:
            pp.error("No video streams found")
            raise cappa.Exit(code=1)
        if not self.audio_streams:
            pp.error("No audio streams found")
            raise cappa.Exit(code=1)

        # Skip reordering if streams are already in the desired order (video->audio->subtitles)
        if not self._need_stream_reorder():
            return [f"{SYMBOL_CHECK} No streams to reorder"]

        # Use -c copy to avoid re-encoding when reordering streams
        initial_command = ["-c", "copy"]

        # Flatten stream lists into ffmpeg mapping commands while preserving desired order
        command = initial_command + [
            item
            for stream_list in [self.video_streams, self.audio_streams, self.subtitle_streams]
            for stream in stream_list
            for item in ["-map", f"0:{stream.index}"]
        ]

        return self._run_ffmpeg(command, title="Reorder streams", step="reorder")

    def video_to_1080p(self) -> list[str]:
        """Convert video resolution to 1080p.

        Scale video dimensions to 1920x1080 while maintaining aspect ratio. Only converts videos larger than 1080p unless forced.

        Returns:
            list[str]: Substep messages describing the outcome, for the caller to display. Empty when conversion cannot proceed.
        """
        # Find first valid video stream, excluding thumbnail/image streams
        video_stream = next(
            stream
            for stream in self.probe_box.streams
            if stream.codec_type == CodecTypes.VIDEO
            and stream.codec_type.value not in EXCLUDED_VIDEO_CODECS
        )

        if not video_stream:
            pp.error("No video stream found")
            return []

        # Skip downscaling if video is already 1080p or smaller, unless forced
        if not settings.force and getattr(video_stream, "width", 0) <= 1920:  # noqa: PLR2004
            return [f"{SYMBOL_CHECK} No convert to 1080p needed"]

        # Use -2 for height to maintain aspect ratio while ensuring even dimensions for compatibility
        command: list[str] = [
            "-filter:v",
            "scale=width=1920:height=-2",
            "-c:a",
            "copy",
            "-c:s",
            "copy",
        ]

        return self._run_ffmpeg(command, title="Convert to 1080p", step="1080p")

    def ffprobe_json(self) -> dict:
        """Run ffprobe on the video file and return the JSON response.

        Returns:
            dict: A dictionary containing the ffprobe output with information about the video file's streams, format, and metadata.
        """
        return run_ffprobe(self.path)
