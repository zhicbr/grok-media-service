"""
Grok Media Service - 测试客户端
用于验证本地服务是否正常工作。

用法：
    python test_client.py --mode status   # 检查认证与服务状态
    python test_client.py --mode image    # 测试生成一张图片
    python test_client.py --mode video    # 测试生成一个短视频
"""

import urllib.request
import json
import argparse
import sys

if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_URL = "http://127.0.0.1:8088"
# 确保对本地 127.0.0.1 的请求不走代理
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
urllib.request.install_opener(opener)


def check_status():
    print(f"正在检查服务状态: {BASE_URL}/v1/auth/status ...")
    try:
        req = urllib.request.Request(f"{BASE_URL}/v1/auth/status")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
            print("== 服务响应成功 ==")
            print(f"状态: {data.get('status')}")
            print(f"账号邮箱: {data.get('email')}")
            print(f"Token 到期时间: {data.get('expires_at')}")
            print(f"剩余有效秒数: {data.get('remaining_seconds')} 秒")
            return True
    except Exception as e:
        print(f"连接失败: {e}")
        print("请确认服务是否已启动 (运行 python server.py)")
        return False


def test_image_generation(prompt="一只在咖啡馆窗边喝咖啡的小猫，温暖午后阳光，写实电影感"):
    print(f"\n正在测试生图接口，提示词: '{prompt}'...")
    payload = {
        "prompt": prompt,
        "aspect_ratio": "16:9"
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/v1/images/generations",
        data=data_bytes,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            res = json.loads(resp.read().decode())
            first_item = res.get("data", [{}])[0]
            print("== 图片生成成功！==")
            print(f"本地访问链接: {first_item.get('url')}")
            print(f"本地硬盘路径: {first_item.get('local_path')}")
    except urllib.error.HTTPError as e:
        print(f"生成失败 HTTP {e.code}: {e.read().decode()}")
    except Exception as e:
        print(f"发生错误: {e}")


def test_video_generation(prompt="微风吹过草地，蒲公英随风轻轻飘散，慢动作电影质感"):
    print(f"\n正在测试视频生成(同步模式)，提示词: '{prompt}'...")
    print("服务将在后台自动轮询直到生成完成，请稍候约 30-90 秒...")
    payload = {
        "prompt": prompt,
        "duration": 6,
        "resolution_name": "480p",
        "aspect_ratio": "16:9"
    }
    data_bytes = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE_URL}/v1/videos/sync",
        data=data_bytes,
        headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            res = json.loads(resp.read().decode())
            print("== 视频生成并下载成功！==")
            print(f"状态: {res.get('status')}")
            print(f"本地视频链接: {res.get('local_url')}")
            print(f"本地硬盘路径: {res.get('local_path')}")
    except urllib.error.HTTPError as e:
        print(f"视频生成失败 HTTP {e.code}: {e.read().decode()}")
    except Exception as e:
        print(f"发生错误: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["status", "image", "video"], default="status")
    parser.add_argument("--prompt", type=str, default=None)
    args = parser.parse_args()

    if args.mode == "status":
        check_status()
    elif args.mode == "image":
        if check_status():
            test_image_generation(args.prompt or "一只在咖啡馆窗边喝咖啡的小猫，温暖午后阳光，写实电影感")
    elif args.mode == "video":
        if check_status():
            test_video_generation(args.prompt or "微风吹过草地，蒲公英随风轻轻飘散，慢动作电影质感")
