from mutagen.flac import FLAC
import os
import subprocess
import locale

def get_md5(input_file, bps):
    if bps == 16:
        command = [
            'ffmpeg',
            '-i', input_file,
            '-map', '0:a',
            '-f', 'md5',
            '-'
        ]
    elif bps == 24:
        command = [
            'ffmpeg',
            '-i', input_file,
            '-map', '0:a',
            '-c:a', 'pcm_s24le',
            '-f', 'md5',
            '-'
        ]
    else:
        raise Exception('只支持16bit/24bit')
    try:
        p = subprocess.run(command, capture_output=True, check=True)
        stdout = str(p.stdout.strip(), locale.getdefaultlocale()[1])
        hex_string = stdout.split('=')[1]
        if not hex_string:
            raise
        decimal_value = int(hex_string, 16)
        if not decimal_value:
            raise
        return decimal_value
    except subprocess.CalledProcessError as e:
        raise Exception(f'FFmpeg Error: {e.stderr.decode()}')
    except:
        raise

files = [f for f in os.listdir('.') if os.path.isfile(f) and f.endswith('.flac')]

for root, dirs, files in os.walk('.'):
    flac_files = [f for f in files if f.endswith('.flac')]
    for flac_file in flac_files:
        file = os.path.join(root, flac_file)
        flac = FLAC(file)
        bps = flac.info.bits_per_sample
        try:
            md5 = get_md5(file, bps)
            flac.info.md5_signature = md5
            flac.save()
            print(f'写入成功：{file}')
        except Exception as e:
            print(f'错误：{e}')
