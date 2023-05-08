import hashlib
import json
import time

import requests

# 需要修改的地方
app_id = ''
token = ''
app_secret = ''


def download(track_id, fmt_id, app_secret):
    ts = time.time()
    request_sig = "trackgetFileUrlformat_id{}intentstreamtrack_id{}{}{}".format(fmt_id, track_id, ts, app_secret)
    request_sig_hased = hashlib.md5(request_sig.encode("utf-8")).hexdigest()
    params = {
        'request_ts': ts,
        'request_sig': request_sig_hased,
        'track_id': track_id,
        'format_id': fmt_id,
        'intent': 'stream',
    }
    file_url_response = requests.post('https://www.qobuz.com/api.json/0.2/track/getFileUrl', params=params,
                                      headers=headers, stream=True)
    print('正在下载：{}'.format(track_id))
    file_url = json.loads(file_url_response.text)['url']
    stream = requests.get(file_url, stream=True)
    with open(str(track_id) + '.flac', 'wb') as f:
        f.write(stream.content)


album_id = input('请输入album ID： ')
fmt_id = 27
headers = {
    'X-App-Id': app_id,
    'X-User-Auth-Token': token
}
album_info = requests.post('https://www.qobuz.com/api.json/0.2/album/get?album_id=' + album_id + '&offset=0',
                           headers=headers)
tracks = json.loads(album_info.text)['tracks']['items']
track_ids = []
for track in tracks:
    track_ids.append(track['id'])
print(track_ids)
for track_id in track_ids:
    download(track_id, fmt_id, app_secret)
