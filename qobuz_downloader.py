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


def get_config_value(option, section='api'):
    if not config.has_section(section):
        raise ValueError(f"Section not found: {section}")

    if not config.has_option(section, option):
        raise ValueError(f"Option not found: {section} - {option}")

    value = config.get(section, option)
    if not value:
        raise ValueError(f"Configuration value is empty: {section} - {option}")
    return value


def get_logger():
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
    return logger


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


def get_title(album_info):
    """
    拼接标题和版本
    """
    album_title = album_info.get('title')
    album_version = album_info.get('version')
    if album_version:
        return f'{album_title.strip()} ({album_version.strip()})'
    return album_info.get('title').strip()


def secure_filename(filename):
    for char in filename:
        if char in ['\\', '/', ':', '*', '?', '"', '<', '>', '|']:
            filename = filename.replace(char, '_')
    return filename.strip()


def get_dest_dir(album_artist, album_title):
    """
    拼接文件路径
    """
    dest_dir = os.path.join(secure_filename(album_artist), secure_filename(album_title).strip('.'))
    if not os.path.exists(dest_dir):
        os.makedirs(dest_dir)
    return dest_dir


def get_file_url(track_id):
    """
    获取下载链接
    """
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
    response = session.post('https://www.qobuz.com/api.json/0.2/track/getFileUrl', params=params, headers=headers,
                            stream=True)
    return json.loads(response.text)['url']


def download_cover(album_info, cover):
    """
    下载专辑封面
    """
    if not os.path.exists(cover):
        url = album_info.get('image', {}).get('large', '').replace('_600.jpg', '_max.jpg')
        response = session.get(url)
        with open(cover, 'wb') as f:
            f.write(response.content)


def download(album_info: dict, track_info: dict):
    """
    从专辑中下载单曲
    :param album_info: 专辑信息
    :param track_info: 单曲信息
    :return:
    """
    album_artist = album_info.get('artist', {}).get('name', '')
    album_title = get_title(album_info)
    track_title = get_title(track_info)
    track_number = track_info.get('track_number')
    track_id = track_info.get('id')
    file_url = get_file_url(track_id)
    filename = secure_filename(f'{track_number:02d}-{track_title}.flac')
    file = os.path.join(get_dest_dir(album_artist, album_title), filename)
    temp_file = file + '.downloading'
    if os.path.exists(file):
        logger.info(f'{file}: The file already exists, skipped.')
        return
    response = session.get(file_url, stream=True)
    file_size = int(response.headers['Content-Length'])
    if not os.path.exists(file):
        if os.path.exists(temp_file):
            start_byte = os.path.getsize(temp_file)
        else:
            start_byte = 0
        if start_byte != file_size:
            bar = tqdm.tqdm(total=file_size, initial=start_byte, unit='B', unit_scale=True, desc=f'{track_number:02d}')
            stream = session.get(file_url, stream=True, headers={'Range': f'bytes={start_byte}-{file_size}'})
            if stream.status_code != 206:
                raise Exception('Server does not support partial downloads.')
            with open(temp_file, 'ab') as f:
                for chunk in stream.iter_content(chunk_size=1024):
                    f.write(chunk)
                    bar.update(len(chunk))
        os.rename(temp_file, file)
        try:
            add_tags(file, album_info, track_info)
        except Exception as e:
            logger.error(f'An error occurred while adding tags: {e}')


def add_tags(filename, album_info, track_info):
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
    album_title = get_title(album_info)
    tags['album'] = album_title
    tags['albumartist'] = album_artist
    tags['albumid'] = album_info.get('id', '')
    tags['comment'] = album_info.get('url', '')
    tags['date'] = album_info.get('release_date_original', '')
    tags['releasetype'] = album_info.get('release_type', '')
    tags['upc'] = album_info.get('upc', '')
    tags['disctotal'] = str(album_info.get('media_count', ''))
    tags['tracktotal'] = str(album_info.get('tracks_count', ''))
    tags['grouping'] = album_info.get('genre', {}).get('name', '')
    tags['label'] = album_info.get('label', {}).get('name', '')
    tags['genre'] = album_info.get('genres_list', [''])[0]
    tags['title'] = get_title(track_info)
    tags['copyright'] = track_info.get('copyright', '')
    tags['isrc'] = track_info.get('isrc', '')
    tags['trackid'] = str(track_info.get('id', ''))
    tags['discnumber'] = str(track_info.get('media_number', ''))
    tags['tracknumber'] = str(track_info.get('track_number', ''))
    tags['artist'] = track_info.get('performer', {}).get('name', '')
    tags['composer'] = track_info.get('composer', {}).get('name', '')
    tags['performers'] = track_info.get('performers', '').replace('\r', '').replace(' - ', '\n')
    cover = os.path.join(get_dest_dir(album_artist, album_title), 'cover.jpg')
    download_cover(album_info, cover)
    picture = Picture()
    picture.type = 3
    picture.mime = 'image/jpeg'
    picture.data = open(cover, 'rb').read()
    flac.clear_pictures()
    flac.add_picture(picture)
    flac.save()


def download_albums(album_ids):
    """
    下载专辑列表
    """
    if not album_ids:
        raise Exception('No albums to download.')
    logger.info(f'Preparing to download a total of {len(album_ids)} albums.')
    for album_id in album_ids:
        album_info = get_album_info(album_id)
        tracks_info = album_info['tracks']['items']
        album_artist = album_info['artist']['name']
        album_title = get_title(album_info)
        logger.info(f'Preparing to download a total of {len(tracks_info)} tracks from the album "{album_artist} - {album_title}".')
        for track_info in tracks_info:
            track_number = track_info['track_number']
            track_title = track_info['title']
            logger.info(f'Downloading track "{track_number:02d}-{track_title}".')
            download(album_info, track_info)


def download_single(album_id, single_ids):
    """
    从专辑中下载单曲列表
    """
    album_info = get_album_info(album_id)
    tracks_info = album_info['tracks']['items']
    logger.info(f'Preparing to download tracks: {",".join([str(num) for num in track_numbers])}.')
    for track_number in track_numbers:
        if track_number > len(tracks_info):
            logger.error(f'{track_number}: The specified track number does not exist, skipped.')
            continue
        track_info = tracks_info[track_number - 1]
        track_number = track_info['track_number']
        track_title = track_info['title']
        logger.info(f'Downloading track "{track_number:02d}-{track_title}".')
        download(album_info, track_info)




# 创建一个配置解析器对象
config = configparser.ConfigParser()
# 读取配置文件
config.read('config.ini')
# 获取配置
app_id = get_config_value('app_id')
token = get_config_value('token')
app_secret = get_config_value('app_secret')
max_retries = config.getint('general', 'max_retries', fallback=5)  # 请求失败后重试次数
retry_delay = config.getint('general', 'retry_delay', fallback=2)  # 重试间隔
log_file = config.get('general', 'log_file', fallback='qd.log')  # 日志

# 获取日志记录器
logger = get_logger()



# 创建会话对象并设置适配器
session = requests.Session()
adapter = RetryAdapter()
session.mount('http://', adapter)
session.mount('https://', adapter)

# 创建参数解析器
parser = argparse.ArgumentParser()
parser.add_argument('--album', '-a', nargs=1)
parser.add_argument('--track', '-t', nargs='*')
parser.add_argument('positional_arg', nargs='*')
parser.add_argument("-f", "--file", action="store_true")
args = parser.parse_args()

fmt_id = 27
headers = {
    'X-App-Id': app_id,
    'X-User-Auth-Token': token,
}

if args.file:
    with open('albums.txt', 'rt') as file:
        lines = [line.strip() for line in file]
    download_albums(lines)
else:
    album_id = args.album[0] if args.album else args.positional_arg.pop(0) if args.positional_arg else None
    if not album_id:
        raise Exception('No album id specified')
    track_numbers = args.track or args.positional_arg
    if not args.track:
        args.positional_arg = []
    if args.positional_arg:
        raise Exception(args.positional_arg)
    track_numbers = ' '.join(track_numbers)
    track_numbers = sorted({int(item.strip()) for item in re.split(r'[\D]+', track_numbers) if item.strip() and int(item.strip()) != 0})
    if not track_numbers:
        download_albums([album_id])
    else:
        download_single(album_id, track_numbers)
