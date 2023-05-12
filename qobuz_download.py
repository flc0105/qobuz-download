import argparse
import hashlib
import json
import os
import sys
import time

import requests
from mutagen import File
from mutagen.flac import Picture

# 需要修改的地方
app_id = ''
token = ''
app_secret = ''

fmt_id = 27
headers = {
    'X-App-Id': app_id,
    'X-User-Auth-Token': token
}


def get_album_info(album_id) -> dict:
    """
    获取专辑信息
    :param album_id: 专辑id
    :return: 专辑信息
    """
    album_info = requests.post(f'https://www.qobuz.com/api.json/0.2/album/get?album_id={album_id}&offset=0',
                               headers=headers)
    album_info = json.loads(album_info.text)
    if 'tracks' not in album_info:
        raise Exception('没有找到该专辑的信息')
    return album_info


def download(album_info, track_number):
    """
    下载单曲
    :param album_info: 专辑信息
    :param track_number: 音轨号
    :return:
    """
    track = album_info['tracks']['items'][track_number - 1]
    track_id = track['id']
    track_title = track['title']
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
    file_url_response = requests.post('https://www.qobuz.com/api.json/0.2/track/getFileUrl', params=params,
                                      headers=headers, stream=True)
    file_url = json.loads(file_url_response.text)['url']
    filename = f'{track_number:02d} - {track_title}.flac'
    print(f'正在下载：{filename}')
    stream = requests.get(file_url, stream=True)
    with open(filename, 'wb') as f:
        f.write(stream.content)
    add_tag(filename, album_info, track)
    print(f'下载完成：{filename}')


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
    tags['albumartist'] = album_info['artist']['name']
    tags['album'] = album_info['title']
    tags['date'] = album_info['release_date_original']
    tags['copyright'] = album_info['copyright']
    tags['comment'] = album_info['url']
    tags['upc'] = album_info['upc']
    tags['genre'] = album_info['genre']['name']
    tags['releasetype'] = album_info['release_type']
    tags['totaldiscs'] = str(album_info['media_count'])
    tags['totaltracks'] = str(album_info['tracks_count'])
    tags['artist'] = track['performer']['name']
    tags['title'] = track['title']
    tags['tracknumber'] = str(track['track_number'])
    tags['discnumber'] = str(track['media_number'])
    tags['composer'] = track['composer']['name']
    tags['performers'] = track['performers']
    tags['isrc'] = track['isrc']
    url = album_info['image']['large'].replace('_600.jpg', '_max.jpg')
    response = requests.get(url)
    picture = Picture()
    picture.data = response.content
    picture.type = 3
    picture.mime = 'image/jpeg'
    flac.clear_pictures()
    flac.add_picture(picture)
    flac.save()


parser = argparse.ArgumentParser()
parser.add_argument("-a", dest="album")
parser.add_argument("-s", dest="single")
args = parser.parse_args()

if args.single:
    album_info = get_album_info(args.single)
    tracks_info = album_info['tracks']['items']
    album_artist = album_info['artist']['name']
    album_title = album_info['title']
    print(f'{album_artist} - {album_title}\n')
    for track in tracks_info:
        track_number = track['track_number']
        track_title = track['title']
        print(f'{track_number:02d} - {track_title}')
    while 1:
        track_number = input('\n输入要下载的音轨号，输入0结束下载：')
        if not track_number:
            continue
        if track_number == '0':
            sys.exit(0)
        try:
            track_number = int(track_number)
        except:
            continue
        download(album_info, track_number)
elif args.album:
    album_info = get_album_info(args.album)
    tracks_info = album_info['tracks']['items']
    album_artist = album_info['artist']['name']
    album_title = album_info['title']
    print(f'{album_artist} - {album_title}\n')
    total_tracks = len(tracks_info)
    for i in range(1, total_tracks + 1):
        download(album_info, i)
else:
    print("未提供有效的参数")
    sys.exit(1)
