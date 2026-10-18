# -*- coding: utf-8 -*-
"""download_retail.py — 带断线自动重试的 UCI 数据集下载器"""
import os
import sys
import time
import urllib.request

URL = 'https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip'
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..',
                   'downloads', 'online_retail_II.zip')
OUT = os.path.abspath(OUT)

MAX_ROUND = 60          # 最多重试轮数
TIMEOUT = 120           # 单次连接超时（秒）
STALL = 90              # 单轮最长静默时间（秒）

os.makedirs(os.path.dirname(OUT), exist_ok=True)


def log(msg):
    print(f'[{time.strftime("%H:%M:%S")}] {msg}', flush=True)


def download():
    have = os.path.getsize(OUT) if os.path.exists(OUT) else 0
    for attempt in range(1, MAX_ROUND + 1):
        try:
            req = urllib.request.Request(URL)
            if have:
                req.add_header('Range', f'bytes={have}-')
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                status = resp.status
                total = resp.length
                total_mb = (total / 1e6) if total else 0.0
                log(f'第{attempt}轮连接成功 status={status} 已下载={have/1e6:.1f}MB '
                    f'本段大小={total_mb:.1f}MB')
                mode = 'ab' if (have and status == 206) else 'wb'
                if mode == 'wb':
                    have = 0
                written = 0
                with open(OUT, mode) as f:
                    while True:
                        chunk = resp.read(256 * 1024)
                        if not chunk:
                            break
                        f.write(chunk)
                        written += len(chunk)
                have += written
                log(f'本轮写入 {written/1e6:.1f}MB，累计 {have/1e6:.1f}MB')
                if status == 200 and written >= 40_000_000:
                    log('下载完成')
                    return True
        except Exception as e:
            log(f'第{attempt}轮失败: {type(e).__name__}: {e}')
        time.sleep(2)
    log('超过最大重试轮数，放弃')
    return False


if __name__ == '__main__':
    ok = download()
    sys.exit(0 if ok else 1)
