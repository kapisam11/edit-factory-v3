import requests
from pathlib import Path

MODEL_DIR = Path('.models/mobilenet_ssd')
MODEL_DIR.mkdir(parents=True, exist_ok=True)

FILES = {
    'deploy.prototxt': 'https://raw.githubusercontent.com/chuanqi305/MobileNet-SSD/master/deploy.prototxt',
    'mobilenet.caffemodel': 'https://github.com/chuanqi305/MobileNet-SSD/raw/master/mobilenet_iter_73000.caffemodel'
}

def download(url, dest: Path):
    print('Downloading', url)
    r = requests.get(url, stream=True, timeout=60)
    r.raise_for_status()
    with open(dest, 'wb') as f:
        for chunk in r.iter_content(1024 * 64):
            if chunk:
                f.write(chunk)

def main():
    for name, url in FILES.items():
        dest = MODEL_DIR / name
        if dest.exists():
            print('Exists:', dest)
            continue
        try:
            download(url, dest)
            print('Saved', dest)
        except Exception as e:
            print('Failed to download', url, e)

if __name__ == '__main__':
    main()
