"""Download compatible source streams; ffmpeg only merges, never encodes."""
import sys
from pathlib import Path
from yt_dlp import YoutubeDL

class Quiet:
    def debug(self, *args): pass
    def warning(self, *args): pass
    def error(self, *args): pass

def main():
    directory = Path(sys.argv[2]).resolve()
    options = dict(quiet=True, no_warnings=True, logger=Quiet(), noplaylist=True,
        socket_timeout=15, retries=1, max_filesize=100_000_000,
        format='bestvideo[ext=mp4][vcodec^=avc1]+bestaudio[ext=m4a]/best[ext=mp4][vcodec^=avc1]',
        merge_output_format='mp4', outtmpl=str(directory / 'source.%(ext)s'))
    with YoutubeDL(options) as downloader:
        downloader.download([sys.argv[1]])
    files = list(directory.glob('source.mp4'))
    if len(files) != 1 or not 0 < files[0].stat().st_size <= 100_000_000:
        raise ValueError('no_compatible_original')
    print(str(files[0]))

if __name__ == '__main__':
    try: main()
    except Exception:
        # URLs and platform responses can contain private identifiers.
        print('Unable to download a compatible original.', file=sys.stderr)
        sys.exit(1)

