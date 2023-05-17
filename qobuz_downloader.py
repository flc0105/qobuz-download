import argparse
import configparser
import hashlib
import json
import logging
import os
import re
import sys
import time

import requests
import tqdm
from mutagen import File
from mutagen.flac import Picture
from requests.adapters import HTTPAdapter


def get_config_value(option, section='api'):
    if not config.has_section(section):
        raise ValueError(f"Section not found: {section}")

    if not config.has_option(section, option):
        raise ValueError(f"Option not found: {section} - {option}")

    value = config.get(section, option)
    if not value:
        raise ValueError(f"Configuration value is empty: {section} - {option}")
    return value


def secure_filename(filename):
    for char in filename:
        if char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
            filename = filename.replace(char, '_')
    return filename.strip()


# 创建一个配置解析器对象
config = configparser.ConfigParser()
# 读取配置文件
config.read('config.ini')

app_id = get_config_value('app_id')
token = get_config_value('token')
app_secret = get_config_value('app_secret')
max_retries = config.getint('general', 'max_retries', fallback=5)  # 请求失败后重试次数
retry_delay = config.getint('general', 'retry_delay', fallback=2)  # 重试间隔
log_file = config.get('general', 'log_file', fallback='qd.log')  # 日志路径

# 创建日志记录器
logger = logging.getLogger()
# 设置日志级别
logger.setLevel(logging.DEBUG)
# 创建控制台处理器和文件处理器
console_handler = logging.StreamHandler()
file_handler = logging.FileHandler(log_file)
# 配置控制台处理器和文件处理器的日志级别
console_handler.setLevel(logging.INFO)
file_handler.setLevel(logging.DEBUG)
# 创建日志记录的格式
log_format = logging.Formatter('[%(asctime)s] %(levelname)s: %(message)s', datefmt='%Y-%m-%d %H:%M:%S')
# 将格式应用于控制台处理器和文件处理器
console_handler.setFormatter(log_format)
file_handler.setFormatter(log_format)
# 将处理器添加到日志记录器
logger.addHandler(console_handler)
logger.addHandler(file_handler)

logger = logging.getLogger(__name__)


# 创建自定义的适配器类
class RetryAdapter(HTTPAdapter):

    def send(self, request, **kwargs):
        retries = 0
        while retries < max_retries:
            try:
                response = super().send(request, **kwargs)
                response.raise_for_status()  # 检查响应状态码，如果不是 200 则抛出异常
                return response
            except requests.exceptions.RequestException as e:
                logger.error(f"Error sending request: {e}")
                retries += 1
                if retries < max_retries:
                    logger.info(f"Retrying ({retries}/{max_retries}) in {retry_delay} seconds...")
                    time.sleep(retry_delay)
        raise Exception("Exceeded maximum number of retries")


# 创建会话对象并设置适配器
session = requests.Session()
adapter = RetryAdapter()
session.mount('http://', adapter)
session.mount('https://', adapter)

fmt_id = 27
headers = {
    'X-App-Id': app_id,
    'X-User-Auth-Token': token,
}


def get_album_info(album_id: str) -> dict:
    """
    获取专辑信息
    :param album_id: 专辑id
    :return: 专辑信息
    """
    album_info = session.post(f'https://www.qobuz.com/api.json/0.2/album/get?album_id={album_id}&offset=0',
                              headers=headers)
    logger.debug(album_info.text)
    album_info = json.loads(album_info.text)
    if 'tracks' not in album_info:
        raise Exception('Failed to find information for the album.')
    return album_info


def download(album_info: dict, track_info: dict):
    """
    从专辑中下载单曲
    :param album_info: 专辑信息
    :param track_info: 单曲信息
    :return:
    """
    album_artist = album_info['artist']['name']
    album_title = album_info.get('title', '')
    if album_info.get('version'):
        album_title += f'({album_info.get("version")})'
    track_title = track_info.get('title', '')
    if track_info.get('version'):
        track_title += f'({track_info.get("version")})'
    track_number = track_info['track_number']
    track_id = track_info['id']
    ts = time.time()
    request_sig = "trackgetFileUrlformat_id{}intentstreamtrack_id{}{}{}".format(fmt_id, track_id, ts, app_secret)
    request_sig_hashed = hashlib.md5(request_sig.encode("utf-8")).hexdigest()
    params = {
        'request_ts': ts,
        'request_sig': request_sig_hashed,
        'track_id': track_id,
        'format_id': fmt_id,
        'intent': 'stream',
    }
    file_url_response = session.post('https://www.qobuz.com/api.json/0.2/track/getFileUrl', params=params,
                                     headers=headers, stream=True)
    file_url = json.loads(file_url_response.text)['url']
    filename = secure_filename(f'{track_number:02d}-{track_title}.flac')
    dest_dir = os.path.join(secure_filename(f'{album_artist}'), secure_filename(f'{album_title}'))
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)
    cover_file = os.path.join(dest_dir, 'cover.jpg')
    if not os.path.exists(cover_file):
        url = album_info.get('image', {}).get('large', '').replace('_600.jpg', '_max.jpg')
        response = session.get(url)
        with open(cover_file, 'wb') as f:
            f.write(response.content)
    dest_file = os.path.join(dest_dir, filename)
    temp_file = os.path.join(dest_dir, filename + '.downloading')
    if os.path.exists(dest_file):
        logger.info(f'{dest_file}: The file already exists, skipped.')
        return
    response = session.get(file_url, stream=True)
    file_size = int(response.headers['Content-Length'])
    if not os.path.exists(dest_file):
        if os.path.exists(temp_file):
            first_byte = os.path.getsize(temp_file)
        else:
            first_byte = 0
        if first_byte != file_size:
            bar = tqdm.tqdm(total=file_size, initial=first_byte, unit='B', unit_scale=True, desc=f'{track_number:02d}')
            stream = session.get(file_url, stream=True, headers={'Range': f'bytes={first_byte}-{file_size}'})
            if stream.status_code != 206:
                raise Exception('Server does not support partial downloads.')
            with open(temp_file, 'ab') as f:
                for chunk in stream.iter_content(chunk_size=1024):
                    f.write(chunk)
                    bar.update(len(chunk))
        os.rename(temp_file, dest_file)
        try:
            add_tag(dest_file, album_info, track_info)
        except Exception as e:
            logger.error(f'Error occurs while adding tags: {e}')


def add_tag(filename, album_info, track):
    """
    为音乐文件添加标签
    :param filename: 文件名
    :param album_info: 专辑信息
    :param track: 单曲信息
    :return:
    """
    flac = File(filename)
    flac.add_tags()
    tags = flac.tags
    album_artist = album_info.get('artist', {}).get('name', '')
    tags['albumartist'] = album_artist
    album_title = album_info.get('title', '')
    if album_info.get('version'):
        album_title += f'({album_info.get("version")})'
    tags['album'] = album_title
    tags['date'] = album_info.get('release_date_original', '')
    tags['comment'] = album_info.get('url', '')
    tags['upc'] = album_info.get('upc', '')
    tags['grouping'] = album_info.get('genre', {}).get('name', '')
    tags['genre'] = album_info.get('genres_list', [''])[0]
    tags['releasetype'] = album_info.get('release_type', '')
    tags['disctotal'] = str(album_info.get('media_count', ''))
    tags['tracktotal'] = str(album_info.get('tracks_count', ''))
    tags['label'] = album_info.get('label', {}).get('name', '')
    tags['artist'] = track.get('performer', {}).get('name', '')
    track_title = track.get('title', '')
    if track.get('version'):
        track_title += f'({track.get("version")})'
    tags['title'] = track_title
    tags['tracknumber'] = str(track.get('track_number', ''))
    tags['albumid'] = str(album_info.get('id', ''))
    tags['trackid'] = str(track.get('id', ''))
    tags['discnumber'] = str(track.get('media_number', ''))
    tags['composer'] = track.get('composer', {}).get('name', '')
    tags['copyright'] = track.get('copyright', '')
    tags['performers'] = track.get('performers', '').replace(' - ', '\n')
    tags['isrc'] = track.get('isrc', '')
    dest_dir = os.path.join(secure_filename(f'{album_artist}'), secure_filename(f'{album_title}'))
    cover_file = os.path.join(dest_dir, 'cover.jpg')
    if not os.path.exists(cover_file):
        url = album_info.get('image', {}).get('large', '').replace('_600.jpg', '_max.jpg')
        response = session.get(url)
        with open(cover_file, 'wb') as f:
            f.write(response.content)
    picture = Picture()
    # url = album_info.get('image', {}).get('large', '').replace('_600.jpg', '_max.jpg')
    # response = requests.get(url)
    # picture.data = response.content
    with open(cover_file, 'rb') as f:
        picture.data = f.read()
    picture.type = 3
    picture.mime = 'image/jpeg'
    flac.clear_pictures()
    flac.add_picture(picture)
    flac.save()


parser = argparse.ArgumentParser()
parser.add_argument('--album', '-a', nargs=1)
parser.add_argument('--track', '-t', nargs='*')
parser.add_argument('positional_arg', nargs='*')
args = parser.parse_args()

album_id = args.album[0] if args.album else args.positional_arg.pop(0) if args.positional_arg else None
track_numbers = args.track or args.positional_arg

if not args.track:
    args.positional_arg = []

if not album_id:
    raise Exception('No album id specified')

if args.positional_arg:
    raise Exception(args.positional_arg)

track_numbers = ' '.join(track_numbers)
track_numbers = sorted(
    {int(item.strip()) for item in re.split(r'[\D]+', track_numbers) if item.strip() and int(item.strip()) != 0})

album_info = get_album_info(album_id)
tracks_info = album_info['tracks']['items']

album_artist = album_info['artist']['name']
album_title = album_info.get('title', '')
if album_info.get('version'):
    album_title += f'({album_info.get("version")})'
total_tracks = len(tracks_info)

if not track_numbers:
    logger.info(f'Preparing to download all {total_tracks} tracks from the album "{album_artist} - {album_title}".')
    for track_info in tracks_info:
        track_number = track_info['track_number']
        track_title = track_info['title']
        logger.info(f'Downloading track "{track_number:02d}-{track_title}".')
        download(album_info, track_info)
else:
    logger.info(f'Preparing to download tracks: {", ".join([str(num) for num in track_numbers])}.')
    for track_number in track_numbers:
        if track_number > len(tracks_info):
            logger.error('The specified track number does not exist. Skipped.')
            continue
        track_info = tracks_info[track_number - 1]
        track_number = track_info['track_number']
        track_title = track_info['title']
        logger.info(f'Downloading track "{track_number:02d}-{track_title}".')
        download(album_info, track_info)
