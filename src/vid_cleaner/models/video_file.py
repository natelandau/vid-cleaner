"""VideoFile model."""

import atexit
import re
import uuid
from pathlib import Path
from typing import Optional

import typer
from ffmpeg_progress_yield import FfmpegProgress
from iso639 import Lang
from loguru import logger
from pydantic import BaseModel
from rich.progress import Progress
from rich.table import Table

from vid_cleaner.constants import (
    APP_DIR,
    EXCLUDED_VIDEO_CODECS,
    FFMPEG_APPEND,
    FFMPEG_PREPEND,
    H265_CODECS,
    SYMBOL_CHECK,
    AudioLayout,
    CodecTypes,
)
from vid_cleaner.utils import console, ffprobe, query_radarr, query_sonarr, query_tmdb


def cleanup_on_exit(video_file: "VideoFile") -> None:  # pragma: no cover
    """Cleanup temporary files on exit.

    Args:
        video_file (VideoFile): The VideoFile object to perform cleanup on.

    Returns:
        None
    """
    video_file.cleanup()


class VideoStream(BaseModel):
    """VideoStream model."""

    index: int
    codec_name: str
    codec_long_name: str
    codec_type: CodecTypes
    duration: Optional[str]
    width: Optional[int]
    height: Optional[int]
    bps: Optional[int]
    sample_rate: Optional[int]
    language: Optional[str]
    channels: Optional[AudioLayout]
    channel_layout: Optional[str]
    layout: Optional[str]
    title: Optional[str]


class VideoProbe(BaseModel):
    """VideoProbe model."""

    name: str
    streams: list[VideoStream]
    format_name: Optional[str]
    format_long_name: Optional[str]
    duration: Optional[str]
    start_time: Optional[float]
    size: Optional[int]
    bit_rate: Optional[int]
    json_data: dict

    @classmethod
    def parse_probe_response(cls, json_obj: dict, stem: str) -> "VideoProbe":
        """Parse ffprobe json object."""
        # Find name
        if "title" in json_obj["format"]["tags"]:
            name = json_obj["format"]["tags"]["title"]
        elif "filename" in json_obj["format"]:
            name = json_obj["format"]["filename"]
        else:
            name = stem

        # Find streams
        streams = [
            VideoStream(
                index=stream["index"],
                codec_name=stream["codec_name"],
                codec_long_name=stream["codec_long_name"],
                codec_type=CodecTypes(stream["codec_type"].lower()),
                duration=stream.get("duration", None),
                width=stream.get("width", None),
                height=stream.get("height", None),
                bps=stream.get("tags", {}).get("BPS", None),
                sample_rate=stream.get("sample_rate", None),
                language=stream.get("language", None)
                or stream.get("tags", {}).get("language", None),
                channels=AudioLayout(stream.get("channels"))
                if stream.get("channels", False)
                else None,
                channel_layout=stream.get("channel_layout", None),
                layout=stream.get("layout", None),
                title=stream.get("tags", {}).get("title", None),
            )
            for stream in json_obj["streams"]
        ]

        return cls(
            name=name,
            format_name=json_obj["format"].get("format_name", None),
            format_long_name=json_obj["format"].get("format_long_name", None),
            duration=json_obj["format"].get("duration", None),
            start_time=json_obj["format"].get("start_time", None),
            size=json_obj["format"].get("size", None),
            bit_rate=json_obj["format"].get("bit_rate", None),
            streams=streams,
            json_data=json_obj,
        )

    def as_table(self) -> Table:
        """Return the video probe as a rich table."""
        table = Table(title=self.name)
        table.add_column("#")
        table.add_column("Type")
        table.add_column("Codec Name")
        table.add_column("Language")
        table.add_column("Channels")
        table.add_column("Channel Layout")
        table.add_column("Width")
        table.add_column("Height")
        table.add_column("Title")

        for stream in self.streams:
            table.add_row(
                str(stream.index),
                stream.codec_type.value,
                stream.codec_name,
                stream.language,
                str(stream.channels.value) if stream.channels else "",
                stream.channel_layout or "",
                str(stream.width) if stream.width else "",
                str(stream.height) if stream.height else "",
                stream.title or "",
            )

        return table


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

    @staticmethod
    def _downmix_to_stereo(streams: list[VideoStream]) -> list[str]:
        """Generate a partial ffmpeg command to downmix audio streams to stereo if needed.

        Analyze the provided audio streams and construct a command to downmix 5.1 or 7.1 audio
        streams to stereo. Handle cases where stereo is already present or needs to be created
        from surround sound streams.

        Args:
            streams (list[VideoStream]): List of audio stream dictionaries.

        Returns:
            list[str]: A list of strings forming part of an ffmpeg command for audio downmixing.
        """
        downmix_command: list[str] = []
        new_index = 0
        has_stereo = False
        surround5 = []  # index of 5.1 streams
        surround7 = []  # index of 7.1 streams

        for stream in streams:
            if stream.channels == AudioLayout.STEREO:
                has_stereo = True
            if stream.channels == AudioLayout.SURROUND5:
                surround5.append(stream)
            if stream.channels == AudioLayout.SURROUND7:
                surround7.append(stream)

        if not has_stereo and surround5:
            for surround5_stream in surround5:
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
                    ]
                )
                new_index += 1
                has_stereo = True

        if not has_stereo and surround7:
            logger.debug(
                "PROCESS AUDIO: Audio track is 5 channel, no 2 channel exists. Creating 2 channel from 5 channel"
            )

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
                    ]
                )
                new_index += 1

        logger.trace(f"PROCESS AUDIO: Downmix command: {downmix_command}")
        return downmix_command

    def _find_original_language(self, verbosity: int) -> Lang:  # pragma: no cover
        """Determine the original language of the video.

        Query various sources like IMDb, TMDB, Radarr, and Sonarr to identify the original language.
        Perform this operation only once and cache the result. Return the determined language or
        None if it cannot be found.

        Args:
            verbosity (int): The verbosity level of the logger.

        Returns:
            Lang: An object representing the original language, or None if not found.
        """
        # Only run the API calls once
        if self.ran_language_check:
            return self.language

        original_language = None

        # Try to find the IMDb ID
        match = re.search(r"(tt\d+)", self.stem)
        imdb_id = match.group(0) if match else self._query_arr_apps_for_imdb_id()

        # Query TMDB for the original language
        response = query_tmdb(imdb_id, verbosity=verbosity) if imdb_id else None

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

    def _get_probe(self) -> VideoProbe:  # pragma: no cover
        """Retrieve the ffprobe probe information for the video.

        Fetch detailed information about the video file using ffprobe. Optionally filter
        the information by a specific key.

        Returns:
            VideoProbe: The ffprobe probe information.
        """
        input_path, _ = self._get_input_and_output()

        return VideoProbe.parse_probe_response(ffprobe(input_path), self.stem)

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
        suffix = suffix or self.suffix
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
    def _process_video(streams: list[VideoStream]) -> list[str]:
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
            if stream.codec_name.lower() in EXCLUDED_VIDEO_CODECS:
                continue

            command.extend(["-map", f"0:{stream.index}"])

        logger.trace(f"PROCESS VIDEO: {command}")
        return command

    def _process_subtitles(
        self,
        streams: list[VideoStream],
        langs_to_keep: list[str],
        keep_commentary: bool,
        keep_all_subtitles: bool,
        keep_local_subtitles: bool,
        verbosity: int,
        subs_drop_local: bool = False,
    ) -> list[str]:
        """Construct a command list for processing subtitle streams.

        Analyze and filter subtitle streams based on language preferences, commentary options,
        and other criteria. Build an ffmpeg command list accordingly.

        Args:
            streams (list[VideoStream]): A list of subtitle stream objects.
            langs_to_keep (list[str]): Languages of subtitles to keep.
            keep_commentary (bool): Flag to keep or discard commentary subtitles.
            keep_all_subtitles (bool): Flag to keep all subtitles regardless of language.
            keep_local_subtitles (bool): Flag to keep subtitles with 'undetermined' language or in the specified list.
            subs_drop_local (bool, optional): Drop subtitles if the original language is not in the list. Defaults to False.
            verbosity (int): The verbosity level of the logger.

        Returns:
            list[str]: A list of strings forming part of an ffmpeg command for subtitle processing.
        """
        command: list[str] = []

        langs = [Lang(lang) for lang in langs_to_keep]

        # Find original language
        if not subs_drop_local:
            original_language = self._find_original_language(verbosity=verbosity)

        # Return no streams if no languages are specified
        if not keep_all_subtitles and not keep_local_subtitles and subs_drop_local:
            return command

        for stream in streams:
            if (
                not keep_commentary
                and stream.title is not None
                and re.search(r"commentary|sdh|description", stream.title, re.IGNORECASE)
            ):
                logger.trace(rf"PROCESS SUBTITLES: Remove stream #{stream.index} \[commentary]")
                continue

            if keep_all_subtitles:
                command.extend(["-map", f"0:{stream.index}"])
                continue

            if stream.language:
                if keep_local_subtitles and (
                    stream.language.lower() == "und" or Lang(stream.language) in langs
                ):
                    command.extend(["-map", f"0:{stream.index}"])
                    continue

                if (
                    not subs_drop_local
                    and langs
                    and original_language not in langs
                    and (stream.language.lower == "und" or Lang(stream.language) in langs)
                ):
                    command.extend(["-map", f"0:{stream.index}"])
                    continue

            logger.trace(f"PROCESS SUBTITLES: Remove stream #{stream.index}")

        logger.trace(f"PROCESS SUBTITLES: {command}")
        return command

    def _process_audio(
        self,
        streams: list[VideoStream],
        langs_to_keep: list[str],
        drop_original_audio: bool,
        keep_commentary: bool,
        downmix_stereo: bool,
        verbosity: int,
    ) -> tuple[list[str], list[str]]:
        """Construct commands for processing audio streams.

        Analyze and process audio streams based on language, commentary, and downmixing criteria.
        Generate ffmpeg commands for keeping or altering audio streams as required.

        Args:
            streams (list[VideoStream]): A list of audio stream objects.
            langs_to_keep (list[str]): Languages of audio to keep.
            drop_original_audio (bool): Flag to drop the original audio track.
            keep_commentary (bool): Flag to keep or discard commentary audio tracks.
            downmix_stereo (bool): Flag to downmix to stereo if required.
            verbosity (int): The verbosity level of the logger.

        Returns:
            tuple[list[str], list[str]]: A tuple containing two lists of strings forming part of an ffmpeg command for audio processing.
        """
        command: list[str] = []

        # Turn language codes into iso639 objects
        langs = [Lang(lang) for lang in langs_to_keep]

        # Add original language to list of languages to keep
        if not drop_original_audio:
            original_language = self._find_original_language(verbosity=verbosity)
            if original_language and original_language not in langs:
                langs.append(original_language)

        streams_to_keep = []
        for stream in streams:
            # Keep unknown language streams
            if not stream.language:
                command.extend(["-map", f"0:{stream.index}"])
                streams_to_keep.append(stream)
                continue

            # Remove commentary streams
            if (
                not keep_commentary
                and stream.title
                and re.search(r"commentary|sdh|description", stream.title, re.IGNORECASE)
            ):
                logger.trace(rf"PROCESS AUDIO: Remove stream #{stream.index} \[commentary]")
                continue

            # Keep streams with specified languages
            if stream.language == "und" or Lang(stream.language) in langs:
                command.extend(["-map", f"0:{stream.index}"])
                streams_to_keep.append(stream)
                continue

            logger.trace(f"PROCESS AUDIO: Remove stream #{stream.index}")

        # Failsafe to cancel processing if all streams would be removed following this plugin. We don't want no audio.
        if not command:
            for stream in streams:
                command.extend(["-map", f"0:{stream.index}"])
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
        self,
        command: list[str],
        title: str,
        suffix: str | None = None,
        step: str | None = None,
        dry_run: bool = False,
    ) -> Path:
        """Execute an ffmpeg command.

        Run the provided ffmpeg command, showing progress and logging information. Determine
        input and output paths, and manage temporary files related to the operation.

        Args:
            command (list[str]): The ffmpeg command to execute.
            dry_run (bool, optional): Run in dry run mode. Defaults to False.
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

        if dry_run:
            console.rule(f"{title} (dry run)")
            console.print(f"[code]{' '.join(cmd)}[/code]")
            return output_path

        # Run ffmpeg
        ff = FfmpegProgress(cmd)

        with Progress(transient=True) as progress:
            task = progress.add_task(f"{title}…", total=100)
            for complete in ff.run_command_with_progress():
                progress.update(task, completed=complete)

        logger.info(f"{SYMBOL_CHECK} {title}")

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

    def clip(
        self,
        start: str,
        duration: str,
        dry_run: bool = False,
    ) -> Path:
        """Clip a segment from the video.

        Extract a specific portion of the video based on the given start time and duration.
        Utilize ffmpeg to perform the clipping operation.

        Args:
            start (str): Start time of the clip.
            duration (str): Duration of the clip.
            dry_run (bool, optional): Run in dry run mode. Defaults to False.

        Returns:
            Path: Path to the clipped video file.
        """
        # Build ffmpeg command
        ffmpeg_command: list[str] = ["-ss", start, "-t", duration, "-map", "0", "-c", "copy"]

        # Run ffmpeg
        return self._run_ffmpeg(ffmpeg_command, title="Clip video", step="clip", dry_run=dry_run)

    def convert_to_h265(
        self,
        force: bool = False,
        dry_run: bool = False,
    ) -> Path:
        """Convert the video to H.265 codec format.

        Check if conversion is necessary and perform it if so. This involves calculating the
        bitrate, building the ffmpeg command, and running it. Return the path to the converted
        video or the original video if conversion isn't needed.

        Args:
            force (bool, optional): Flag to force conversion even if the video is already H.265. Defaults to False.
            dry_run (bool, optional): Run in dry run mode. Defaults to False.

        Returns:
            Path: Path to the converted or original video file.
        """
        input_path, _ = self._get_input_and_output()

        # Get ffprobe probe
        probe = self._get_probe()
        video_stream = [  # noqa: RUF015
            stream
            for stream in probe.streams
            if stream.codec_type == CodecTypes.VIDEO
            and stream.codec_name.lower() not in EXCLUDED_VIDEO_CODECS
        ][0]

        # Fail if no video stream is found
        if not video_stream:
            logger.error("No video stream found")
            return input_path

        # Return if video is already H.265
        if not force and video_stream.codec_name.lower() in H265_CODECS:
            logger.warning(
                "H265 ENCODE: Video already H.265 or VP9. Run with `--force` to re-encode. Skipping"
            )
            return input_path

        # Calculate Bitrate
        # ############################
        # Check if duration info is filled, if so times it by 0.0166667 to get time in minutes.
        # If not filled then get duration of stream 0 and do the same.
        stream_duration = float(probe.duration) or float(video_stream.duration)
        if not stream_duration:
            logger.error("Could not calculate video duration")
            return input_path

        duration = stream_duration * 0.0166667

        # Work out currentBitrate using "Bitrate = file size / (number of minutes * .0075)"
        # Used from here https://blog.frame.io/2017/03/06/calculate-video-bitrates/

        stat = input_path.stat()
        logger.trace(f"File size: {stat}")
        file_size_megabytes = stat.st_size / 1000000

        current_bitrate = int(file_size_megabytes / (duration * 0.0075))
        target_bitrate = int(file_size_megabytes / (duration * 0.0075) / 2)
        min_bitrate = int(current_bitrate * 0.7)
        max_bitrate = int(current_bitrate * 1.3)

        # Build FFMPEG Command
        command: list[str] = ["-map", "0", "-c:v", "libx265"]
        # Create bitrate command
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
            ]
        )

        # Copy audio and subtitles
        command.extend(["-c:a", "copy", "-c:s", "copy"])
        # Run ffmpeg
        return self._run_ffmpeg(command, title="Convert to H.265", step="h265", dry_run=dry_run)

    def convert_to_vp9(
        self,
        force: bool = False,
        dry_run: bool = False,
    ) -> Path:
        """Convert the video to the VP9 codec format.

        Verify if conversion is required and proceed with it using ffmpeg. This method specifically
        targets the VP9 video codec. Return the path to the converted video or the original video
        if conversion is not necessary.

        Args:
            dry_run (bool, optional): Run in dry run mode. Defaults to False.
            force (bool, optional): Flag to force conversion even if the video is already VP9. Defaults to False.

        Returns:
            Path: Path to the converted or original video file.
        """
        input_path, _ = self._get_input_and_output()

        # Get ffprobe probe
        probe = self._get_probe()
        video_stream = [  # noqa: RUF015
            stream
            for stream in probe.streams
            if stream.codec_type == CodecTypes.VIDEO
            and stream.codec_name.lower() not in EXCLUDED_VIDEO_CODECS
        ][0]

        # Fail if no video stream is found
        if not video_stream:
            logger.error("No video stream found")
            return input_path

        # Return if video is already H.265
        if not force and video_stream.codec_name.lower() in H265_CODECS:
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
            "-dn",
            "-map_chapters",
            "-1",
        ]

        # Copy subtitles
        command.extend(["-c:s", "copy"])

        # Run ffmpeg
        return self._run_ffmpeg(
            command, title="Convert to vp9", suffix=".webm", step="vp9", dry_run=dry_run
        )

    def process_streams(
        self,
        langs_to_keep: list[str],
        drop_original_audio: bool,
        keep_commentary: bool,
        downmix_stereo: bool,
        keep_all_subtitles: bool,
        keep_local_subtitles: bool,
        subs_drop_local: bool,
        verbosity: int,
        dry_run: bool = False,
    ) -> Path:
        """Process the video file according to specified audio and subtitle preferences.

        Execute the necessary steps to process the video file, including managing audio and subtitle streams.  Keep or discard audio streams based on specified languages, commentary preferences, and downmix settings. Similarly, filter subtitle streams based on language preferences and criteria such as keeping commentary or local subtitles. Perform the processing using ffmpeg and return the path to the processed video file.

        Args:
            dry_run (bool, optional): Run in dry run mode. Defaults to False.
            langs_to_keep (list[str]): List of language codes for audio and subtitles to retain.
            drop_original_audio (bool): Flag to determine whether to drop the original audio track.
            keep_commentary (bool): Flag to determine whether to keep or discard commentary audio tracks.
            downmix_stereo (bool): Flag to downmix to stereo if the original is not stereo.
            keep_all_subtitles (bool): Flag to keep all subtitle tracks, regardless of language.
            keep_local_subtitles (bool): Flag to keep subtitles with 'undetermined' language or in the specified list.
            subs_drop_local (bool): Flag to drop subtitles if the original language is not in the list.
            verbosity (int): The verbosity level of the logger.

        Returns:
            Path: Path to the processed video file.
        """
        probe = self._get_probe()

        video_streams = [s for s in probe.streams if s.codec_type == CodecTypes.VIDEO]
        audio_streams = [s for s in probe.streams if s.codec_type == CodecTypes.AUDIO]
        subtitle_streams = [s for s in probe.streams if s.codec_type == CodecTypes.SUBTITLE]

        video_map_command = self._process_video(video_streams)
        audio_map_command, downmix_command = self._process_audio(
            streams=audio_streams,
            langs_to_keep=langs_to_keep,
            drop_original_audio=drop_original_audio,
            keep_commentary=keep_commentary,
            downmix_stereo=downmix_stereo,
            verbosity=verbosity,
        )
        subtitle_map_command = self._process_subtitles(
            streams=subtitle_streams,
            langs_to_keep=langs_to_keep,
            keep_commentary=keep_commentary,
            keep_all_subtitles=keep_all_subtitles,
            keep_local_subtitles=keep_local_subtitles,
            verbosity=verbosity,
            subs_drop_local=subs_drop_local,
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
            dry_run=dry_run,
        )

    def reorder_streams(
        self,
        dry_run: bool = False,
    ) -> Path:
        """Reorder the media streams within the video file.

        Arrange the streams in the video file so that video streams appear first, followed by audio streams, and then subtitle streams. Exclude certain types of video streams like 'mjpeg' and 'png'.

        Returns:
            Path: Path to the video file with reordered streams.
        """
        probe = self._get_probe()

        video_streams = [
            s
            for s in probe.streams
            if s.codec_type == CodecTypes.VIDEO
            and s.codec_name.lower() not in EXCLUDED_VIDEO_CODECS
        ]
        audio_streams = [s for s in probe.streams if s.codec_type == CodecTypes.AUDIO]
        subtitle_streams = [s for s in probe.streams if s.codec_type == CodecTypes.SUBTITLE]

        # Fail if no video or audio streams are found
        if not video_streams:
            logger.error("No video streams found")
            raise typer.Exit(1)
        if not audio_streams:
            logger.error("No audio streams found")
            raise typer.Exit(1)

        # Check if reordering is needed
        reorder = any(
            stream.index != i
            for i, stream in enumerate(video_streams + audio_streams + subtitle_streams)
        )

        if not reorder:
            logger.info(f"{SYMBOL_CHECK} No streams to reorder")
            input_path, _ = self._get_input_and_output()
            return input_path

        # Initial command parts
        initial_command = ["-c", "copy"]

        # Build the command list using list comprehension and concatenation
        command = initial_command + [
            item
            for stream_list in [video_streams, audio_streams, subtitle_streams]
            for stream in stream_list
            for item in ["-map", f"0:{stream.index}"]
        ]

        # Run ffmpeg
        return self._run_ffmpeg(command, title="Reorder streams", step="reorder", dry_run=dry_run)

    def video_to_1080p(self, force: bool = False, dry_run: bool = False) -> Path:
        """Convert the video to 1080p resolution."""
        input_path, _ = self._get_input_and_output()

        # Get ffprobe probe
        probe = self._get_probe()

        video_stream = [  # noqa: RUF015
            stream
            for stream in probe.streams
            if stream.codec_type == CodecTypes.VIDEO
            and stream.codec_type.value not in EXCLUDED_VIDEO_CODECS
        ][0]

        # Fail if no video stream is found
        if not video_stream:
            logger.error("No video stream found")
            return input_path

        # Return if video is not 4K
        if not force and getattr(video_stream, "width", 0) <= 1920:  # noqa: PLR2004
            logger.info(f"{SYMBOL_CHECK} No convert to 1080p needed")
            return input_path

        # Build ffmpeg command
        command: list[str] = [
            "-filter:v",
            "scale=width=1920:height=-2",
            "-c:a",
            "copy",
            "-c:s",
            "copy",
        ]

        # Run ffmpeg
        return self._run_ffmpeg(command, title="Convert to 1080p", step="1080p", dry_run=dry_run)

    def as_stream_table(self) -> Table:
        """Return the video probe as a rich table."""
        probe = self._get_probe()
        return probe.as_table()

    def ffprobe_json(self) -> dict:
        """Return the ffprobe json response."""
        return self._get_probe().json_data
