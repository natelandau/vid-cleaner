"""VideoFile model."""

import atexit
import re
from pathlib import Path
from typing import assert_never

import cappa
from box import Box
from ffmpeg_progress_yield import FfmpegProgress
from iso639 import Lang
from nclutils import console, pp
from rich.markdown import Markdown
from rich.progress import Progress

from vid_cleaner import settings
from vid_cleaner.constants import (
    COMMENTARY_STREAM_TITLE_REGEX,
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
from vid_cleaner.utils import get_probe_as_box, query_radarr, query_sonarr, query_tmdb, run_ffprobe

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
        self.language: Lang = None
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
            if stream.title and re.search(
                COMMENTARY_STREAM_TITLE_REGEX, stream.title, re.IGNORECASE
            ):
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
    def _downmix_to_stereo(streams: list[Box]) -> list[str]:
        """Generate a partial ffmpeg command to downmix audio streams to stereo if needed.

        Analyze the provided audio streams and construct a command to downmix 5.1 or 7.1 audio streams to stereo. Handle cases where stereo is already present or needs to be created from surround sound streams.

        Args:
            streams (list[Box]): List of audio stream dictionaries.

        Returns:
            list[str]: A list of strings forming part of an ffmpeg command for audio downmixing.
        """
        downmix_command: list[str] = []
        new_index = 0
        has_stereo = False
        surround5 = []  # Track 5.1 streams for potential downmixing
        surround7 = []  # Track 7.1 streams for potential downmixing

        for stream in streams:
            match stream.channels:
                case AudioLayout.STEREO:
                    has_stereo = True
                case AudioLayout.SURROUND5:
                    surround5.append(stream)
                case AudioLayout.SURROUND7:
                    surround7.append(stream)
                case AudioLayout.MONO:
                    pass
                case _:
                    assert_never(stream.channels)

        if not has_stereo and surround5:
            for surround5_stream in surround5:
                # Custom pan filter to preserve center channel dialogue and add LFE for bass impact
                # Coefficients tuned to maintain dialogue clarity while preserving surround ambiance
                downmix_command.extend(
                    [
                        "-map",
                        f"0:{surround5_stream.index}",
                        f"-c:a:{new_index}",
                        "aac",
                        f"-ac:a:{new_index}",
                        "2",
                        f"-b:a:{new_index}",
                        "256k",
                        f"-filter:a:{new_index}",
                        "pan=stereo|FL=FC+0.30*FL+0.30*FLC+0.30*BL+0.30*SL+0.60*LFE|FR=FC+0.30*FR+0.30*FRC+0.30*BR+0.30*SR+0.60*LFE,loudnorm",
                        f"-ar:a:{new_index}",
                        "48000",
                        f"-metadata:s:a:{new_index}",
                        "title=2.0",
                    ],
                )
                new_index += 1
                has_stereo = True

        if not has_stereo and surround7:
            pp.debug(
                "PROCESS AUDIO: Audio track is 5 channel, no 2 channel exists. Creating 2 channel from 5 channel",
            )
            # For 7.1, use default ffmpeg downmixing since custom pan filter would be too complex
            # and the default algorithm provides good results for 7.1 sources
            for surround7_stream in surround7:
                downmix_command.extend(
                    [
                        "-map",
                        f"0:{surround7_stream.index}",
                        f"-c:a:{new_index}",
                        "aac",
                        f"-ac:a:{new_index}",
                        "2",
                        f"-b:a:{new_index}",
                        "256k",
                        f"-metadata:s:a:{new_index}",
                        "title=2.0",
                    ],
                )
                new_index += 1

        pp.trace(f"PROCESS AUDIO: Downmix command: {downmix_command}")
        return downmix_command

    def _find_original_language(self) -> Lang:  # pragma: no cover
        """Find the original language of the video content.

        Query external APIs (IMDb, TMDB, Radarr, Sonarr) to determine the original language. Cache results to avoid repeated API calls.

        Returns:
            Lang: The determined original language code
        """
        if self.ran_language_check:
            return self.language

        original_language = None

        # Extract IMDb ID from filename if present (e.g. "Movie.Title.tt1234567.mkv")
        match = re.search(r"(tt\d+)", self.stem)
        imdb_id = match.group(0) if match else self._query_arr_apps_for_imdb_id()

        response = query_tmdb(imdb_id) if imdb_id else None

        if response and (tmdb_response := response.get("movie_results", [{}])[0]):
            original_language = tmdb_response.get("original_language")
            pp.trace(f"TMDB: Original language: {original_language}")

        if not original_language:
            pp.debug(f"Could not find original language for: {self.name}")
            return None

        # TMDB uses 'cn' for Chinese but iso639 expects 'zh'
        if original_language == "cn":
            original_language = "zh"

        try:
            language = Lang(original_language)
        except Exception:  # noqa: BLE001
            pp.debug(f"iso639: Could not find language for: {self.name}")
            return None

        self.language = language
        self.ran_language_check = True
        return language

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

        streams_to_keep = []
        for stream in self.audio_streams:
            # Unknown language streams are kept to avoid removing potentially important audio
            if not stream.language:
                command.extend(["-map", f"0:{stream.index}"])
                streams_to_keep.append(stream)
                continue

            # Commentary tracks are often unwanted and take up space
            if (
                not settings.keep_commentary
                and stream.title
                and re.search(COMMENTARY_STREAM_TITLE_REGEX, stream.title, re.IGNORECASE)
            ):
                pp.trace(rf"PROCESS AUDIO: Remove stream #{stream.index} \[commentary]")
                continue

            if stream.language == "und" or Lang(stream.language) in langs:
                command.extend(["-map", f"0:{stream.index}"])
                streams_to_keep.append(stream)
                continue

            pp.trace(f"PROCESS AUDIO: Remove stream #{stream.index}")

        # If all streams would be removed, keep them all to prevent silent video
        if not command:
            for stream in self.audio_streams:
                command.extend(["-map", f"0:{stream.index}"])
                streams_to_keep.append(stream)

        # Create stereo downmix commands if requested
        downmix_command = (
            self._downmix_to_stereo(streams_to_keep) if settings.downmix_stereo else []
        )

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
            if (
                not settings.keep_commentary
                and stream.title is not None
                and re.search(COMMENTARY_STREAM_TITLE_REGEX, stream.title, re.IGNORECASE)
            ):
                pp.trace(rf"PROCESS SUBTITLES: Remove stream #{stream.index} \[commentary]")
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
                    and (stream.language.lower == "und" or Lang(stream.language) in langs)
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
        if response and "movie" in response and "imdbId" in response["parsedMovieInfo"]:
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
    ) -> Path:
        """Execute an ffmpeg command.

        Run the provided ffmpeg command, showing progress and logging information. Determine input and output paths, and manage temporary files related to the operation.

        Args:
            command (list[str]): The ffmpeg command to execute.
            title (str): Title for logging the process.
            suffix (str | None, optional): Suffix for the output file. Use when creating a new container mime type. Defaults to None.
            step (str | None, optional): Step name for file naming. Used when creating a new temporary file. Defaults to None.

        Returns:
            Path: Path to the output file generated by the ffmpeg command.

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
            console.rule(f"{title} (dry run)")
            markdown_command = Markdown(f"```console\n{' '.join(cmd)}\n```")
            console.print(markdown_command)
            return output_path

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

        pp.info(f"{SYMBOL_CHECK} {title}")

        self.temp_file.created_temp_file(output_path)
        pp.trace(f"Created temp file: {output_path}")
        return output_path

    def clip(self, start: str, duration: str) -> Path:
        """Clip a segment from the video.

        Extract a specific portion of the video based on the given start time and duration. Utilize ffmpeg to perform the clipping operation.

        Args:
            start (str): Start time of the clip.
            duration (str): Duration of the clip.

        Returns:
            Path: Path to the clipped video file.
        """
        ffmpeg_command: list[str] = ["-ss", start, "-t", duration, "-map", "0", "-c", "copy"]

        return self._run_ffmpeg(ffmpeg_command, title="Clip video", step="clip")

    def convert_to_h265(self) -> Path:
        """Convert the video to H.265 codec format.

        Check if conversion is necessary and perform it if so. This involves calculating the bitrate, building the ffmpeg command, and running it. Return the path to the converted video or the original video if conversion isn't needed.

        Returns:
            Path: Path to the converted or original video file.
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
            return input_path

        if not settings.force and video_stream.codec_name.lower() in H265_CODECS:
            pp.warning(
                "H265 ENCODE: Video already H.265 or VP9. Run with `--force` to re-encode. Skipping",
            )
            return input_path

        # Calculate target bitrate using Frame.io's formula: https://blog.frame.io/2017/03/06/calculate-video-bitrates/
        # This formula provides good quality while maintaining reasonable file sizes
        stream_duration = float(self.probe_box.duration) or float(video_stream.duration)
        if not stream_duration:
            pp.error("Could not calculate video duration")
            return input_path

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

    def convert_to_vp9(self) -> Path:
        """Convert the video to the VP9 codec format.

        Verify if conversion is required and proceed with it using ffmpeg. This method specifically targets the VP9 video codec. Return the path to the converted video or the original video if conversion is not necessary.

        Returns:
            Path: Path to the converted or original video file.
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
            return input_path

        # Skip re-encoding if already in modern codec unless forced
        if not settings.force and video_stream.codec_name.lower() in H265_CODECS:
            pp.warning(
                "VP9 ENCODE: Video already H.265 or VP9. Run with `--force` to re-encode. Skipping",
            )
            return input_path

        if Path(settings.out_path).suffix != ".webm":
            pp.info(
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

        return self._run_ffmpeg(command, title="Convert to vp9", suffix=".webm", step="vp9")

    def process_streams(self) -> Path:
        """Process the video file according to specified audio and subtitle preferences.

        Execute the necessary steps to process the video file, including managing audio and subtitle streams.  Keep or discard audio streams based on specified languages, commentary preferences, and downmix settings. Similarly, filter subtitle streams based on language preferences and criteria such as keeping commentary or local subtitles. Perform the processing using ffmpeg and return the path to the processed video file.

        Returns:
            Path: Path to the processed video file.
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
            pp.info(f"{SYMBOL_CHECK} No streams to process")
            return self.temp_file.latest_temp_path()

        return self._run_ffmpeg(
            video_map_command
            + audio_map_command
            + subtitle_map_command
            + ["-c", "copy"]
            + downmix_command,
            title=title,
            step="process",
        )

    def reorder_streams(self) -> Path:
        """Reorder the media streams within the video file.

        Arrange the streams in the video file so that video streams appear first, followed by audio streams, and then subtitle streams. Exclude certain types of video streams like 'mjpeg' and 'png'.

        Returns:
            Path: Path to the video file with reordered streams.

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
            pp.info(f"{SYMBOL_CHECK} No streams to reorder")
            return self.temp_file.latest_temp_path()

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

    def video_to_1080p(self) -> Path:
        """Convert video resolution to 1080p.

        Scale video dimensions to 1920x1080 while maintaining aspect ratio. Only converts videos larger than 1080p unless forced.

        Returns:
            Path: Path to the converted video file, or original path if no conversion needed
        """
        input_path = self.temp_file.latest_temp_path()

        # Find first valid video stream, excluding thumbnail/image streams
        video_stream = next(
            stream
            for stream in self.probe_box.streams
            if stream.codec_type == CodecTypes.VIDEO
            and stream.codec_type.value not in EXCLUDED_VIDEO_CODECS
        )

        if not video_stream:
            pp.error("No video stream found")
            return input_path

        # Skip downscaling if video is already 1080p or smaller, unless forced
        if not settings.force and getattr(video_stream, "width", 0) <= 1920:  # noqa: PLR2004
            pp.info(f"{SYMBOL_CHECK} No convert to 1080p needed")
            return input_path

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
