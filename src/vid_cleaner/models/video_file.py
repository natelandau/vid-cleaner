"""VideoFile model."""

import atexit
import re
import uuid
from pathlib import Path

import typer
from ffmpeg_progress_yield import FfmpegProgress
from iso639 import Lang
from loguru import logger
from rich.progress import Progress

from vid_cleaner.constants import (
    APP_DIR,
    EXCLUDED_VIDEO_CODECS,
    FFMPEG_APPEND,
    FFMPEG_PREPEND,
    H265_CODECS,
    AudioLayout,
)
from vid_cleaner.utils import ffprobe, query_radarr, query_sonarr, query_tmdb


def cleanup_on_exit(video_file: "VideoFile") -> None:  # pragma: no cover
    """Cleanup temporary files on exit.

    Args:
        video_file (VideoFile): The VideoFile object to perform cleanup on.

    Returns:
        None
    """
    video_file.cleanup()


class VideoFile:
    """VideoFile model."""

    def __init__(self, path: Path):
        """Initialize VideoFile."""
        self.path = path.expanduser().resolve()
        self.name = path.name
        self.stem = path.stem
        self.parent = path.parent
        self.suffix = path.suffix
        self.suffixes = self.path.suffixes

        self.tmp_dir = APP_DIR / "tmp" / uuid.uuid4().hex
        self.container = self.suffix
        self.language: Lang = None
        self.ran_language_check = False
        self.current_tmp_file: Path | None = None  # Current temporary file
        self.tmp_files: list[Path] = []  # All temporary files created by this VideoFile
        self.tmp_file_number = 1

        # Register cleanup on exit
        atexit.register(cleanup_on_exit, self)

    def _convert_to_h265(self, force: bool = False) -> Path:
        """Convert the video to H.265 codec format.

        Check if conversion is necessary and perform it if so. This involves calculating the
        bitrate, building the ffmpeg command, and running it. Return the path to the converted
        video or the original video if conversion isn't needed.

        Args:
            force (bool, optional): Flag to force conversion even if the video is already H.265. Defaults to False.

        Returns:
            Path: Path to the converted or original video file.
        """
        input_path, _ = self._get_input_and_output()

        # Get ffprobe probe
        probe = self._get_probe()
        video_stream = [  # noqa: RUF015
            stream
            for stream in probe["streams"]
            if stream["codec_type"].lower() == "video"
            and stream["codec_name"].lower() not in EXCLUDED_VIDEO_CODECS
        ][0]

        # Fail if no video stream is found
        if not video_stream:
            logger.error("No video stream found")
            return input_path

        # Return if video is already H.265
        if not force and video_stream["codec_name"].lower() in H265_CODECS:
            logger.warning(
                "H265 ENCODE: Video already H.265 or VP9. Run with `--force` to re-encode. Skipping"
            )
            return input_path

        # Calculate Bitrate
        # ############################
        # Check if duration info is filled, if so times it by 0.0166667 to get time in minutes.
        # If not filled then get duration of stream 0 and do the same.
        stream_duration = float(probe["format"].get("duration", None)) or float(
            video_stream.get("duration", None)
        )
        if not stream_duration:
            logger.error("Could not calculate video duration")
            return input_path

        duration = stream_duration * 0.0166667

        # Work out currentBitrate using "Bitrate = file size / (number of minutes * .0075)"
        # Used from here https://blog.frame.io/2017/03/06/calculate-video-bitrates/
        stat = input_path.stat()
        file_size_megabytes = stat.st_size / 1000000

        current_bitrate = int(file_size_megabytes / (duration * 0.0075))
        target_bitrate = int(file_size_megabytes / (duration * 0.0075) / 2)
        min_bitrate = int(current_bitrate * 0.7)
        max_bitrate = int(current_bitrate * 1.3)

        # Build FFMPEG Command
        command: list[str] = ["-map", "0", "-c:v", "libx265"]

        # Create bitrate command
        command.extend([
            "-b:v",
            f"{target_bitrate}k",
            "-minrate",
            f"{min_bitrate}k",
            "-maxrate",
            f"{max_bitrate}k",
            "-bufsize",
            f"{current_bitrate}k",
        ])

        # Copy audio and subtitles
        command.extend(["-c:a", "copy", "-c:s", "copy"])

        # Run ffmpeg
        return self._run_ffmpeg(command, title="Convert to H.265", step="h265")

    def _convert_to_vp9(self, force: bool = False) -> Path:
        """Convert the video to the VP9 codec format.

        Verify if conversion is required and proceed with it using ffmpeg. This method specifically
        targets the VP9 video codec. Return the path to the converted video or the original video
        if conversion is not necessary.

        Args:
            force (bool, optional): Flag to force conversion even if the video is already VP9. Defaults to False.

        Returns:
            Path: Path to the converted or original video file.
        """
        input_path, _ = self._get_input_and_output()

        # Get ffprobe probe
        probe = self._get_probe()
        video_stream = [  # noqa: RUF015
            stream
            for stream in probe["streams"]
            if stream["codec_type"].lower() == "video"
            and stream["codec_name"].lower() not in EXCLUDED_VIDEO_CODECS
        ][0]

        # Fail if no video stream is found
        if not video_stream:
            logger.error("No video stream found")
            return input_path

        # Return if video is already H.265
        if not force and video_stream["codec_name"].lower() in H265_CODECS:
            logger.warning(
                "VP9 ENCODE: Video already H.265 or VP9. Run with `--force` to re-encode. Skipping"
            )
            return input_path

        # Build ffmpeg command
        command: list[str] = [
            "-map",
            "0",
            "-c:v",
            "libvpx-vp9",
            "-b:v",
            "0",
            "-crf",
            "30",
            "-c:a",
            "libvorbis",
        ]

        # Copy audio and subtitles
        command.extend(["-c:s", "copy"])

        # Run ffmpeg
        return self._run_ffmpeg(command, title="Convert to vp9", suffix=".webm", step="vp9")

    @staticmethod
    def _downmix_to_stereo(streams: list[dict]) -> list[str]:
        """Generate a partial ffmpeg command to downmix audio streams to stereo if needed.

        Analyze the provided audio streams and construct a command to downmix 5.1 or 7.1 audio
        streams to stereo. Handle cases where stereo is already present or needs to be created
        from surround sound streams.

        Args:
            streams (list[dict]): List of audio stream dictionaries.

        Returns:
            list[str]: A list of strings forming part of an ffmpeg command for audio downmixing.
        """
        downmix_command: list[str] = []
        new_index = 0
        has_stereo = False
        surround5 = []  # index of 5.1 streams
        surround7 = []  # index of 7.1 streams

        for stream in streams:
            if stream["channels"] == AudioLayout.STEREO.value:
                has_stereo = True
            if stream["channels"] == AudioLayout.SURROUND5.value:
                surround5.append(stream)
            if stream["channels"] == AudioLayout.SURROUND7.value:
                surround7.append(stream)

        if not has_stereo and surround5:
            for surround5_stream in surround5:
                downmix_command.extend([
                    "-map",
                    f"0:{surround5_stream['index']}",
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
                ])
                new_index += 1
                has_stereo = True

        if not has_stereo and surround7:
            logger.debug(
                "PROCESS AUDIO: Audio track is 5 channel, no 2 channel exists. Creating 2 channel from 5 channel"
            )

            for surround7_stream in surround7:
                downmix_command.extend([
                    "-map",
                    f"0:{surround7_stream['index']}",
                    f"-c:a:{new_index}",
                    "aac",
                    f"-ac:a:{new_index}",
                    "2",
                    f"-b:a:{new_index}",
                    "256k",
                    f"-metadata:s:a:{new_index}",
                    "title=2.0",
                ])
                new_index += 1

        logger.trace(f"PROCESS AUDIO: Downmix command: {downmix_command}")
        return downmix_command

    def _find_original_language(self) -> Lang:  # pragma: no cover
        """Determine the original language of the video.

        Query various sources like IMDb, TMDB, Radarr, and Sonarr to identify the original language.
        Perform this operation only once and cache the result. Return the determined language or
        None if it cannot be found.

        Returns:
            Lang: An object representing the original language, or None if not found.
        """
        # Only run the API calls once
        if self.ran_language_check:
            return self.language

        original_language = None

        # Try to find the IMDb ID
        match = re.search(r"(tt\d{7})", self.stem)
        imdb_id = match.group(0) if match else self._query_arr_apps_for_imdb_id()

        # Query TMDB for the original language
        response = query_tmdb(imdb_id) if imdb_id else None
        if response and (tmdb_response := response.get("movie_results", [{}])[0]):
            original_language = tmdb_response.get("original_language")
            logger.trace(f"TMDB: Original language: {original_language}")

        if not original_language:
            logger.debug(f"Could not find original language for: {self.name}")
            return None

        # If the original language is pulled as Chinese (cn). iso639 expects 'zh' for Chinese.
        if original_language == "cn":
            original_language = "zh"

        try:
            language = Lang(original_language)
        except Exception:  # noqa: BLE001
            logger.debug(f"iso639: Could not find language for: {self.name}")
            return None

        # Set language attribute
        self.language = language
        self.ran_language_check = True
        return language

    def _get_probe(self, key: str | None = None) -> dict:  # pragma: no cover
        """Retrieve the ffprobe probe information for the video.

        Fetch detailed information about the video file using ffprobe. Optionally filter
        the information by a specific key.

        Args:
            key (str | None, optional): A specific key to filter the probe information. Default is None.

        Returns:
            dict: The ffprobe probe information.
        """
        input_path, _ = self._get_input_and_output()

        if key:
            return ffprobe(input_path)[key]

        return ffprobe(input_path)

    def _get_input_and_output(
        self, suffix: str | None = None, step: str | None = None
    ) -> tuple[Path, Path]:
        """Determine input and output file paths for processing steps.

        Calculate the paths based on the current state of the video file, its temporary directory,
        and any specified suffix or processing step name. Create the necessary directories.

        Args:
            suffix (str | None, optional): Suffix for the output file. Defaults to None.
            step (str | None, optional): Name of the processing step. Defaults to None.

        Returns:
            tuple[Path, Path]: A tuple containing the input and output file paths.
        """
        # Input file is most recent temp file or self.path
        input_file = self.tmp_files[-1] if self.tmp_files else self.path

        # Get the output file name
        suffix = suffix if suffix else self.suffix
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        # Create a new tmp file name
        for file in self.tmp_dir.iterdir():
            if file.stem.startswith(f"{self.tmp_file_number}_"):
                self.tmp_file_number += 1

        output_file = self.tmp_dir / f"{self.tmp_file_number}_{step}{suffix}"

        # Remove all but the most recent tmp file to reduce the size of tmp files on disk
        for file in self.tmp_dir.iterdir():
            if file != input_file:
                logger.trace(f"Remove: {file}")
                file.unlink()

        return input_file, output_file

    @staticmethod
    def _process_video(streams: list[dict]) -> list[str]:
        """Create a command list for processing video streams.

        Iterate through the provided video streams and construct a list of ffmpeg commands
        to process them, excluding any streams with codecs in the exclusion list.

        Args:
            streams (list[dict]): A list of video stream dictionaries.

        Returns:
            list[str]: A list of strings forming part of an ffmpeg command for video processing.
        """
        command: list[str] = []
        for stream in streams:
            if stream["codec_name"].lower() in EXCLUDED_VIDEO_CODECS:
                continue

            command.extend(["-map", f"0:{stream['index']}"])

        logger.trace(f"PROCESS VIDEO: {command}")
        return command

    def _process_subtitles(  # noqa: PLR0917
        self,
        streams: list[dict],
        langs_to_keep: list[str],
        keep_commentary: bool,
        keep_all_subtitles: bool,
        keep_local_subtitles: bool,
        keep_subtitles_if_not_original: bool = True,
    ) -> list[str]:
        """Construct a command list for processing subtitle streams.

        Analyze and filter subtitle streams based on language preferences, commentary options,
        and other criteria. Build an ffmpeg command list accordingly.

        Args:
            streams (list[dict]): A list of subtitle stream dictionaries.
            langs_to_keep (list[str]): Languages of subtitles to keep.
            keep_commentary (bool): Flag to keep or discard commentary subtitles.
            keep_all_subtitles (bool): Flag to keep all subtitles regardless of language.
            keep_local_subtitles (bool): Flag to keep subtitles with 'undetermined' language or in the specified list.
            keep_subtitles_if_not_original (bool, optional): Keep subtitles if the original language is not in the list. Defaults to True.

        Returns:
            list[str]: A list of strings forming part of an ffmpeg command for subtitle processing.
        """
        command: list[str] = []

        langs = [Lang(lang) for lang in langs_to_keep]

        # Find original language
        if keep_subtitles_if_not_original:
            original_language = self._find_original_language()

        # Return no streams if no languages are specified
        if (
            not keep_all_subtitles
            and not keep_local_subtitles
            and not keep_subtitles_if_not_original
        ):
            return command

        for stream in streams:
            if (
                not keep_commentary
                and "title" in stream["tags"]
                and re.search(r"commentary|sdh|description", stream["tags"]["title"], re.IGNORECASE)
            ):
                continue

            if keep_all_subtitles:
                command.extend(["-map", f"0:{stream['index']}"])
                continue

            if "tags" in stream and "language" in stream["tags"]:
                if keep_local_subtitles and (
                    stream["tags"]["language"].lower() == "und"
                    or Lang(stream["tags"]["language"]) in langs
                ):
                    command.extend(["-map", f"0:{stream['index']}"])
                    continue

                if (
                    keep_subtitles_if_not_original
                    and langs
                    and original_language not in langs
                    and (
                        stream["tags"]["language"].lower == "und"
                        or Lang(stream["tags"]["language"]) in langs
                    )
                ):
                    command.extend(["-map", f"0:{stream['index']}"])
                    continue

        logger.trace(f"PROCESS SUBTITLES: {command}")
        return command

    def _process_audio(
        self,
        streams: list[dict],
        langs_to_keep: list[str],
        drop_original_audio: bool,
        keep_commentary: bool,
        downmix_stereo: bool,
    ) -> tuple[list[str], list[str]]:
        """Construct commands for processing audio streams.

        Analyze and process audio streams based on language, commentary, and downmixing criteria.
        Generate ffmpeg commands for keeping or altering audio streams as required.

        Args:
            streams (list[dict]): A list of audio stream dictionaries.
            langs_to_keep (list[str]): Languages of audio to keep.
            drop_original_audio (bool): Flag to drop the original audio track.
            keep_commentary (bool): Flag to keep or discard commentary audio tracks.
            downmix_stereo (bool): Flag to downmix to stereo if required.

        Returns:
            tuple[list[str], list[str]]: A tuple containing two lists of strings forming part of an ffmpeg command for audio processing.
        """
        command: list[str] = []
        # Turn language codes into iso639 objects
        langs = [Lang(lang) for lang in langs_to_keep]

        # Add original language to list of languages to keep
        if not drop_original_audio:
            original_language = self._find_original_language()
            if original_language and original_language not in langs:
                langs.append(original_language)

        streams_to_keep = []
        for stream in streams:
            if "tags" not in stream or "language" not in stream["tags"]:
                command.extend(["-map", f"0:{stream['index']}"])
                streams_to_keep.append(stream)
                continue

            # Remove commentary streams
            if (
                not keep_commentary
                and "title" in stream["tags"]
                and re.search(r"commentary|sdh|description", stream["tags"]["title"], re.IGNORECASE)
            ):
                continue

            # Keep streams with specified languages
            if stream["tags"]["language"] == "und" or Lang(stream["tags"]["language"]) in langs:
                command.extend(["-map", f"0:{stream['index']}"])
                streams_to_keep.append(stream)
                continue

        # Failsafe to cancel processing if all streams would be removed following this plugin. We don't want no audio.
        if not command:
            for stream in streams:
                command.extend(["-map", f"0:{stream['index']}"])
                streams_to_keep.append(stream)

        # Downmix to stereo if needed
        downmix_command = self._downmix_to_stereo(streams_to_keep) if downmix_stereo else []

        logger.trace(f"PROCESS AUDIO: {command}")
        return command, downmix_command

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
        self, command: list[str], title: str, suffix: str | None = None, step: str | None = None
    ) -> Path:
        """Execute an ffmpeg command.

        Run the provided ffmpeg command, showing progress and logging information. Determine
        input and output paths, and manage temporary files related to the operation.

        Args:
            command (list[str]): The ffmpeg command to execute.
            title (str): Title for logging the process.
            suffix (str | None, optional): Suffix for the output file. Defaults to None.
            step (str | None, optional): Step name for file naming. Defaults to None.

        Returns:
            Path: Path to the output file generated by the ffmpeg command.
        """
        input_path, output_path = self._get_input_and_output(suffix=suffix, step=step)

        cmd: list[str] = ["ffmpeg", *FFMPEG_PREPEND, "-i", str(input_path)]
        cmd.extend(command)
        cmd.extend([*FFMPEG_APPEND, str(output_path)])

        logger.trace(f"RUN FFMPEG:\n{' '.join(cmd)}")

        # Run ffmpeg
        ff = FfmpegProgress(cmd)

        with Progress(transient=True) as progress:
            task = progress.add_task(f"{title}…", total=100)
            for complete in ff.run_command_with_progress():
                progress.update(task, completed=complete)

        logger.info(f"✔ {title}")

        # Set current temporary file and return path
        self.current_tmp_file = output_path
        self.tmp_files.append(output_path)
        return output_path

    def cleanup(self) -> None:
        """Cleanup temporary files created during video processing.

        Remove all temporary files and directories associated with this VideoFile instance.
        This includes cleaning up any intermediate files generated during processing.
        """
        if self.tmp_dir.exists():
            logger.debug("Clean up temporary files")

            # Clean up temporary files
            for file in self.tmp_dir.iterdir():
                logger.trace(f"Remove: {file}")
                file.unlink()

            # Clean up temporary directory
            logger.trace(f"Remove: {self.tmp_dir}")
            self.tmp_dir.rmdir()

    def clip(self, start: str, duration: str) -> Path:
        """Clip a segment from the video.

        Extract a specific portion of the video based on the given start time and duration.
        Utilize ffmpeg to perform the clipping operation.

        Args:
            start (str): Start time of the clip.
            duration (str): Duration of the clip.

        Returns:
            Path: Path to the clipped video file.
        """
        # Build ffmpeg command
        ffmpeg_command: list[str] = ["-ss", start, "-t", duration, "-map", "0", "-c", "copy"]

        # Run ffmpeg
        return self._run_ffmpeg(ffmpeg_command, title="Clip video", step="clip")

    def process_streams(  # noqa: PLR0917
        self,
        langs_to_keep: list[str],
        drop_original_audio: bool,
        keep_commentary: bool,
        downmix_stereo: bool,
        keep_all_subtitles: bool,
        keep_local_subtitles: bool,
        keep_subtitles_if_not_original: bool,
    ) -> Path:
        """Process the video file according to specified audio and subtitle preferences.

        Execute the necessary steps to process the video file, including managing audio and subtitle streams.  Keep or discard audio streams based on specified languages, commentary preferences, and downmix settings. Similarly, filter subtitle streams based on language preferences and criteria such as keeping commentary or local subtitles. Perform the processing using ffmpeg and return the path to the processed video file.

        Args:
            langs_to_keep (list[str]): List of language codes for audio and subtitles to retain.
            drop_original_audio (bool): Flag to determine whether to drop the original audio track.
            keep_commentary (bool): Flag to determine whether to keep or discard commentary audio tracks.
            downmix_stereo (bool): Flag to downmix to stereo if the original is not stereo.
            keep_all_subtitles (bool): Flag to keep all subtitle tracks, regardless of language.
            keep_local_subtitles (bool): Flag to keep subtitles with 'undetermined' language or in the specified list.
            keep_subtitles_if_not_original (bool): Flag to keep subtitles if the original language is not in the list.

        Returns:
            Path: Path to the processed video file.
        """
        streams = self._get_probe("streams")
        video_streams = [stream for stream in streams if stream["codec_type"].lower() == "video"]
        audio_streams = [stream for stream in streams if stream["codec_type"].lower() == "audio"]
        subtitle_streams = [
            stream for stream in streams if stream["codec_type"].lower() == "subtitle"
        ]

        video_map_command = self._process_video(video_streams)
        audio_map_command, downmix_command = self._process_audio(
            audio_streams,
            langs_to_keep,
            drop_original_audio,
            keep_commentary,
            downmix_stereo,
        )
        subtitle_map_command = self._process_subtitles(
            subtitle_streams,
            langs_to_keep,
            keep_commentary,
            keep_all_subtitles,
            keep_local_subtitles,
            keep_subtitles_if_not_original,
        )

        # Run ffmpeg
        return self._run_ffmpeg(
            video_map_command
            + audio_map_command
            + subtitle_map_command
            + ["-c", "copy"]
            + downmix_command,
            title="Process video",
            step="process",
        )

    def reorder_streams(self) -> Path:
        """Reorder the media streams within the video file.

        Arrange the streams in the video file so that video streams appear first, followed by audio streams, and then subtitle streams. Exclude certain types of video streams like 'mjpeg' and 'png'.


        Returns:
            Path: Path to the video file with reordered streams.
        """
        streams = self._get_probe("streams")

        video_streams = [
            stream
            for stream in streams
            if stream["codec_type"] == "video"
            and stream["codec_name"].lower() not in EXCLUDED_VIDEO_CODECS
        ]
        audio_streams = [stream for stream in streams if stream["codec_type"] == "audio"]
        subtitle_streams = [stream for stream in streams if stream["codec_type"] == "subtitle"]

        # Fail if no video or audio streams are found
        if not video_streams:
            logger.error("No video streams found")
            raise typer.Exit(1)

        if not audio_streams:
            logger.error("No audio streams found")
            raise typer.Exit(1)

        # Build ffmpeg command
        command: list[str] = []

        for stream in video_streams:
            command.extend(["-map", f"0:{stream['index']}"])

        for stream in audio_streams:
            command.extend(["-map", f"0:{stream['index']}"])

        if subtitle_streams:
            for stream in subtitle_streams:
                command.extend(["-map", f"0:{stream['index']}"])

        command.extend(["-c", "copy"])

        # Run ffmpeg
        return self._run_ffmpeg(command, title="Reorder streams", step="reorder")
