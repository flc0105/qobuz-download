import base64
import re
from collections import OrderedDict

import requests

base_url = 'https://play.qobuz.com'
response = requests.get(f'{base_url}/login')
bundle_url_match = re.compile(r'<script src="(/resources/\d+\.\d+\.\d+-[a-z]\d{3}/bundle\.js)"></script>').search(
    response.text)
bundle_url = bundle_url_match.group(1)
print(f'{base_url}{bundle_url}')

'''
response = requests.get(f'{base_url}{bundle_url}')
content = response.text
'''

with open('bundle.js', 'rt', encoding='utf-8') as f:
    content = f.read()

secrets = OrderedDict()

seed_matches = re.compile(r'[a-z]\.initialSeed\("(?P<seed>[\w=]+)",window\.utimezone\.(?P<timezone>[a-z]+)\)').finditer(
    content)
for match in seed_matches:
    seed, timezone = match.group('seed', 'timezone')
    secrets[timezone] = [seed]

keypairs = list(secrets.items())
secrets.move_to_end(keypairs[1][0], last=False)

info_extras_regex = r'name:"\w+/(?P<timezone>{timezones})",info:"(?P<info>[\w=]+)",extras:"(?P<extras>[\w=]+)"'.format(
    timezones='|'.join([timezone.capitalize() for timezone in secrets]))
info_extras_matches = re.finditer(info_extras_regex, content)
for match in info_extras_matches:
    timezone, info, extras = match.group('timezone', 'info', 'extras')
    secrets[timezone.lower()] += [info, extras]

for secret_pair in secrets:
    secrets[secret_pair] = base64.standard_b64decode(''.join(secrets[secret_pair])[:-44]).decode('utf-8')

for k, v in secrets.items():
    print(f'{k}: {v}')
