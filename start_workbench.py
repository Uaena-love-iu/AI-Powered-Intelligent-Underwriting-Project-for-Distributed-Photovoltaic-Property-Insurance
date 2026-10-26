"""Start the local workbench and open its page once the service is ready."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import webbrowser

PROJECT = Path(__file__).resolve().parent
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def ready(base_url):
    try:
        with HTTP.open(base_url + '/health', timeout=1) as response:
            if json.load(response).get('status') != 'ok':
                return False
        with HTTP.open(base_url + '/api/v1/workbench/version', timeout=1) as response:
            if json.load(response).get('version') != '2.0.0':
                return False
        with HTTP.open(base_url + '/evaluation', timeout=1) as response:
            return '<title>整单测试 · 光伏核保</title>' in response.read().decode('utf-8')
    except (OSError, ValueError, urllib.error.URLError):
        return False


def open_page(base_url):
    if not webbrowser.open(base_url + '/evaluation', new=2):
        raise RuntimeError('无法打开默认浏览器，请检查 Windows 的默认浏览器设置。')
    print('工作台已自动在浏览器打开。', flush=True)


def wait_and_open(base_url, process, stopped):
    deadline = time.monotonic() + 60
    while not stopped.is_set() and process.poll() is None:
        if ready(base_url):
            try:
                open_page(base_url)
            except Exception as error:
                print(f'浏览器打开失败：{error}', flush=True)
            return
        if time.monotonic() >= deadline:
            print('服务尚未就绪，请查看上面的启动错误信息。', flush=True)
            return
        stopped.wait(0.25)


def run(port=8002):
    base_url = f'http://127.0.0.1:{port}'
    if ready(base_url):
        print('工作台已在运行，正在打开浏览器。', flush=True)
        open_page(base_url)
        return 0
    process = subprocess.Popen(
        [sys.executable, str(PROJECT / 'start_delivery.py'), '--port', str(port)],
        cwd=PROJECT,
    )
    stopped = threading.Event()
    opener = threading.Thread(target=wait_and_open, args=(base_url, process, stopped), daemon=True)
    opener.start()
    try:
        return process.wait()
    except KeyboardInterrupt:
        return 0
    finally:
        stopped.set()
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        opener.join(timeout=3)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8002)
    args = parser.parse_args()
    sys.exit(run(args.port))
