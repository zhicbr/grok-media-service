"""
Grok Media Service - 本地图像与视频生成服务
基于 Python 标准库开发，零依赖运行。
"""

import os
import sys
import json
import time
import base64
import urllib.request
import urllib.error
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from datetime import datetime, timezone
import threading
import mimetypes
import shutil
import subprocess

# 基础目录定位
if sys.stdout and hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

BASE_DIR = Path(__file__).resolve().parent
CONFIG_FILE = BASE_DIR / "config.json"
OUTPUTS_DIR = BASE_DIR / "outputs"
IMAGES_DIR = OUTPUTS_DIR / "images"
VIDEOS_DIR = OUTPUTS_DIR / "videos"
WEBUI_DIR = BASE_DIR / "webui"

# 确保目录存在
IMAGES_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)


def load_config():
    default_config = {
        "server": {"host": "0.0.0.0", "port": 8000},
        "proxy": "http://127.0.0.1:7890",
        "defaults": {
            "image": {"aspect_ratio": "16:9"},
            "video": {"duration": 6, "resolution_name": "480p", "aspect_ratio": "16:9"}
        },
        "storage": {
            "auto_save": True,
            "images_dir": "outputs/images",
            "videos_dir": "outputs/videos"
        },
        "auth": {
            "auto_read_grok_auth": True,
            "grok_auth_path": str(Path.home() / ".grok" / "auth.json"),
            "auto_refresh_token": True
        }
    }
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                default_config.update(cfg)
        except Exception as e:
            print(f"[Warn] 加载 config.json 失败: {e}，使用默认配置")
    return default_config


CONFIG = load_config()


class AuthManager:
    """管理 Grok SuperGrok 认证凭据与自动续期"""

    def __init__(self, config):
        self.config = config
        self.lock = threading.Lock()
        raw_path = config["auth"].get("grok_auth_path") or "~/.grok/auth.json"
        self.auth_path = Path(os.path.expanduser(raw_path))
        self.current_scope = None
        self.auth_info = {}
        self.load_from_disk()

    def load_from_disk(self):
        with self.lock:
            if not self.auth_path.exists():
                print(f"[Auth] 凭证文件不存在: {self.auth_path}")
                return False
            try:
                with open(self.auth_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for scope, val in data.items():
                    if isinstance(val, dict) and "key" in val:
                        self.current_scope = scope
                        self.auth_info = val
                        print(f"[Auth] 成功载入凭据: {val.get('email')} (scope: {scope})")
                        return True
            except Exception as e:
                print(f"[Auth] 读取 auth.json 错误: {e}")
            return False

    def get_token(self):
        """获取有效 Token，并在需要时自动刷新"""
        with self.lock:
            # 允许环境变量直接覆盖
            env_key = os.environ.get("GROK_API_KEY") or os.environ.get("XAI_API_KEY")
            if env_key:
                return env_key

            if not self.auth_info or "key" not in self.auth_info:
                # 重新尝试从磁盘读取
                self.load_from_disk()

            if not self.auth_info or "key" not in self.auth_info:
                raise RuntimeError("未检测到有效 Grok 登录凭据，请先在终端运行 grok 登录，或配置环境变量 GROK_API_KEY")

            # 检查是否过期或即将过期（提前 180 秒刷新）
            expires_at_str = self.auth_info.get("expires_at")
            if expires_at_str:
                try:
                    exp_dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                    now_dt = datetime.now(timezone.utc)
                    remaining = (exp_dt - now_dt).total_seconds()
                    if remaining <= 180 and self.config["auth"].get("auto_refresh_token", True):
                        print(f"[Auth] Token 剩余 {int(remaining)} 秒，正在自动刷新...")
                        self._do_refresh()
                except Exception as e:
                    print(f"[Auth] 时间戳解析或刷新失败: {e}")

            return self.auth_info["key"]

    def _do_refresh(self):
        """调用 OIDC 刷新接口换取新 Token"""
        rt = self.auth_info.get("refresh_token")
        client_id = self.auth_info.get("oidc_client_id", "b1a00492-073a-47ea-816f-4c329264a828")
        issuer = self.auth_info.get("oidc_issuer", "https://auth.x.ai")
        token_url = f"{issuer.rstrip('/')}/oauth2/token"

        if not rt:
            print("[Auth] 无 refresh_token，跳过刷新")
            return

        payload = {
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": client_id
        }
        encoded = urllib.parse.urlencode(payload).encode("utf-8")
        req = urllib.request.Request(token_url, data=encoded, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")

        proxy = self.config.get("proxy")
        opener = get_urllib_opener(proxy)
        
        for attempt in range(2):
            try:
                with opener.open(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    new_key = data.get("access_token")
                    new_rt = data.get("refresh_token")
                    expires_in = data.get("expires_in", 7200)

                    if new_key:
                        self.auth_info["key"] = new_key
                        if new_rt:
                            self.auth_info["refresh_token"] = new_rt
                        new_exp = datetime.now(timezone.utc).timestamp() + expires_in
                        self.auth_info["expires_at"] = datetime.fromtimestamp(new_exp, timezone.utc).isoformat()
                        print(f"[*] Token 刷新成功！新有效期至: {self.auth_info['expires_at']}")

                        # 写回磁盘
                        self._save_to_disk()
                        return
            except Exception as e:
                print(f"[*] Token 刷新请求失败 (尝试 {attempt+1}/2): {e}")
                time.sleep(1)

    def _save_to_disk(self):
        try:
            with open(self.auth_path, "r", encoding="utf-8") as f:
                disk_data = json.load(f)
            if self.current_scope:
                disk_data[self.current_scope] = self.auth_info
                with open(self.auth_path, "w", encoding="utf-8") as f:
                    json.dump(disk_data, f, indent=2)
                print(f"[*] 已同步写回 {self.auth_path}")
        except Exception as e:
            print(f"[*] 写回 auth.json 失败: {e}")

    def get_status(self):
        # 如果即将过期或已过期，主动尝试刷新
        try:
            self.get_token()
        except Exception:
            pass

        token_present = bool(self.auth_info.get("key"))
        expires_at_str = self.auth_info.get("expires_at")
        expired = False
        remaining_sec = None
        if expires_at_str:
            try:
                exp_dt = datetime.fromisoformat(expires_at_str.replace("Z", "+00:00"))
                now_dt = datetime.now(timezone.utc)
                remaining_sec = int((exp_dt - now_dt).total_seconds())
                expired = remaining_sec <= 0
            except:
                pass
        return {
            "status": "active" if token_present and not expired else "expired" if expired else "unauthenticated",
            "email": self.auth_info.get("email"),
            "user_id": self.auth_info.get("user_id"),
            "scope": self.current_scope,
            "expires_at": expires_at_str,
            "remaining_seconds": remaining_sec,
            "expired": expired
        }


def get_urllib_opener(proxy=None):
    """创建支持代理的 urllib opener"""
    handlers = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    else:
        # 使用系统默认代理环境变量
        handlers.append(urllib.request.ProxyHandler())
    return urllib.request.build_opener(*handlers)


class GrokClient:
    """与 xAI / Grok API 交互的核心客户端"""

    def __init__(self, auth_mgr, config):
        self.auth_mgr = auth_mgr
        self.config = config
        # SuperGrok 会员凭证走 cli-chat-proxy，独立 API Key 走 api.x.ai
        if os.environ.get("XAI_API_KEY"):
            self.base_url = "https://api.x.ai/v1"
        else:
            self.base_url = self.config.get("base_url") or "https://cli-chat-proxy.grok.com/v1"
        self.proxy = config.get("proxy")
        self.opener = get_urllib_opener(self.proxy)

    def _request(self, method, endpoint, payload=None, timeout=60):
        url = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"
        token = self.auth_mgr.get_token()

        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("grok-imagine-image-quality", "high")
        req.add_header("User-Agent", "grok-build/1.0.44")

        try:
            with self.opener.open(req, timeout=timeout) as resp:
                resp_bytes = resp.read()
                return resp.status, json.loads(resp_bytes.decode("utf-8"))
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="ignore")
            print(f"[API Error] {method} {url} -> HTTP {e.code}: {err_body}")
            try:
                err_json = json.loads(err_body)
                return e.code, err_json
            except:
                return e.code, {"error": {"message": err_body, "code": e.code}}
        except Exception as e:
            print(f"[API Error] {method} {url} -> 异常: {e}")
            return 500, {"error": {"message": str(e)}}

    def generate_image(self, prompt, aspect_ratio="auto", model=None):
        payload = {
            "prompt": prompt,
            "aspect_ratio": aspect_ratio or self.config["defaults"]["image"]["aspect_ratio"],
            "response_format": "b64_json"
        }
        if model:
            payload["model"] = model
        return self._request("POST", "/images/generations", payload, timeout=120)

    def edit_image(self, prompt, images, aspect_ratio="auto"):
        # images 可以为 URL 或 base64 data url
        if isinstance(images, str):
            images = [images]
        payload = {
            "prompt": prompt,
            "image": images,
            "aspect_ratio": aspect_ratio or "auto",
            "response_format": "b64_json"
        }
        return self._request("POST", "/images/generations", payload, timeout=120)

    def start_video(self, params):
        payload = {
            "model": params.get("model", "grok-imagine-video-1.5"),
            "prompt": params.get("prompt", ""),
            "duration": params.get("duration", self.config["defaults"]["video"]["duration"]),
            "resolution_name": params.get("resolution_name", self.config["defaults"]["video"]["resolution_name"]),
            "aspect_ratio": params.get("aspect_ratio", self.config["defaults"]["video"]["aspect_ratio"])
        }
        # 可选参数
        for k in ["first_frame", "last_frame", "images", "keyframes", "voices"]:
            if k in params and params[k] is not None:
                payload[k] = params[k]

        return self._request("POST", "/videos/generations", payload, timeout=60)

    def poll_video(self, request_id):
        return self._request("GET", f"/videos/{request_id}", timeout=30)

    def download_file(self, url, dest_path, timeout=60):
        dest_path = Path(dest_path)
        tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")

        # 1. 优先使用系统内置的 curl.exe（Windows 10/11、macOS、Linux 标配，代理连接极稳且速度快）
        curl_bin = shutil.which("curl")
        if curl_bin:
            cmd = [
                curl_bin, "-s", "-S", "-L",
                "--connect-timeout", "15",
                "--max-time", str(timeout),
                "-o", str(tmp_path)
            ]
            if self.proxy:
                cmd.extend(["-x", self.proxy])
            cmd.append(url)

            try:
                res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout + 5)
                if res.returncode == 0 and tmp_path.exists() and tmp_path.stat().st_size > 0:
                    if dest_path.exists():
                        dest_path.unlink()
                    tmp_path.rename(dest_path)
                    print(f"[*] 视频极速下载成功: {dest_path.name} ({dest_path.stat().st_size} 字节)")
                    return True
                else:
                    err_msg = res.stderr.strip()
                    print(f"[*] curl 下载未成功 (code {res.returncode}): {err_msg}，正在回退至原生流式下载...")
            except Exception as e:
                print(f"[*] curl 执行异常: {e}，正在回退至原生流式下载...")

        # 2. 原生 urllib 流式下载（做 Content-Length 完整性校验）
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "grok-build/1.0.44"})
            with self.opener.open(req, timeout=timeout) as resp:
                expected_len = int(resp.headers.get("Content-Length", 0))
                with open(tmp_path, "wb") as f:
                    downloaded = 0
                    while chunk := resp.read(64 * 1024):
                        f.write(chunk)
                        downloaded += len(chunk)
                if expected_len > 0 and downloaded < expected_len:
                    raise IOError(f"下载不完整: 预期 {expected_len} 字节，实际仅下载 {downloaded} 字节")

            if dest_path.exists():
                dest_path.unlink()
            tmp_path.rename(dest_path)
            print(f"[*] 原生下载成功: {dest_path.name} ({dest_path.stat().st_size} 字节)")
            return True
        except Exception as e:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass
            print(f"[*] 下载视频遇到异常: {e}")
            raise e


# 初始化全局单例
AUTH_MANAGER = AuthManager(CONFIG)
GROK_CLIENT = GrokClient(AUTH_MANAGER, CONFIG)


class GrokMediaHandler(SimpleHTTPRequestHandler):
    """HTTP 请求处理器，提供 REST API 和静态文件服务"""

    def __init__(self, *args, **kwargs):
        # 静态文件根目录设为 BASE_DIR
        super().__init__(*args, directory=str(BASE_DIR), **kwargs)

    def _send_json(self, status_code, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._add_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _add_cors_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS, PUT, DELETE")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, Accept, Origin, User-Agent")

    def do_OPTIONS(self):
        self.send_response(204)
        self._add_cors_headers()
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # 根路径返回 WebUI
        if path == "/" or path == "/index.html":
            ui_index = WEBUI_DIR / "index.html"
            if ui_index.exists():
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                content = ui_index.read_bytes()
                self.send_header("Content-Length", str(len(content)))
                self._add_cors_headers()
                self.end_headers()
                self.wfile.write(content)
                return

        # 状态检查
        if path == "/v1/auth/status":
            status = AUTH_MANAGER.get_status()
            self._send_json(200, status)
            return

        # 历史记录列表
        if path == "/v1/history":
            self._handle_history()
            return

        # 轮询视频生成状态: /v1/videos/{request_id}
        if path.startswith("/v1/videos/"):
            request_id = path.replace("/v1/videos/", "").strip("/")
            if request_id and not request_id.startswith("sync"):
                self._handle_poll_video(request_id)
                return

        # 静态资源请求 (/outputs/* 或 /webui/*)
        if path.startswith("/outputs/") or path.startswith("/webui/"):
            super().do_GET()
            return

        self._send_json(404, {"error": "Not Found", "path": path})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_len) if content_len > 0 else b"{}"

        try:
            body = json.loads(raw_body.decode("utf-8")) if raw_body else {}
        except Exception:
            self._send_json(400, {"error": "Invalid JSON Body"})
            return

        # 刷新 Token
        if path == "/v1/auth/refresh":
            try:
                AUTH_MANAGER._do_refresh()
                self._send_json(200, AUTH_MANAGER.get_status())
            except Exception as e:
                self._send_json(500, {"error": str(e)})
            return

        # 1. 文本生图接口 (OpenAI 兼容 /v1/images/generations)
        if path == "/v1/images/generations":
            self._handle_image_generation(body)
            return

        # 2. 图像编辑接口 (/v1/images/edits)
        if path in ["/v1/images/edits", "/v1/images/edit"]:
            self._handle_image_edit(body)
            return

        # 3. 视频生成 - 异步任务提交 (/v1/videos/generations)
        if path == "/v1/videos/generations":
            self._handle_video_generation(body)
            return

        # 4. 视频生成 - 一键同步等待并下载 (/v1/videos/sync)
        if path in ["/v1/videos/sync", "/v1/videos/create_and_wait"]:
            self._handle_video_sync(body)
            return

        self._send_json(404, {"error": "Not Found", "path": path})

    def _handle_image_generation(self, body):
        prompt = body.get("prompt")
        if not prompt:
            self._send_json(400, {"error": "Missing required field: prompt"})
            return

        aspect_ratio = body.get("aspect_ratio", CONFIG["defaults"]["image"]["aspect_ratio"])
        model = body.get("model")

        status_code, resp_data = GROK_CLIENT.generate_image(prompt, aspect_ratio, model)
        if status_code != 200:
            self._send_json(status_code, resp_data)
            return

        # 保存图片到 outputs/images
        data_list = resp_data.get("data", [])
        host = self.headers.get("Host", f"127.0.0.1:{CONFIG['server']['port']}")

        for idx, item in enumerate(data_list):
            b64_str = item.get("b64_json")
            if b64_str:
                img_bytes = base64.b64decode(b64_str)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"img_{timestamp}_{idx + 1}.jpg"
                filepath = IMAGES_DIR / filename
                filepath.write_bytes(img_bytes)

                # 添加本地直接访问链接
                local_url = f"http://{host}/outputs/images/{filename}"
                item["url"] = local_url
                item["local_path"] = str(filepath)

        self._send_json(200, resp_data)

    def _handle_image_edit(self, body):
        prompt = body.get("prompt")
        image = body.get("image")
        if not prompt or not image:
            self._send_json(400, {"error": "Missing required fields: prompt, image"})
            return

        aspect_ratio = body.get("aspect_ratio", "auto")

        # 支持传入本地文件路径，自动读取转为 Base64
        if isinstance(image, str) and (Path(image).is_file() or ":" in image or "/" in image):
            local_p = Path(image)
            if local_p.exists() and local_p.is_file():
                mime = mimetypes.guess_type(local_p.name)[0] or "image/jpeg"
                b64_data = base64.b64encode(local_p.read_bytes()).decode("ascii")
                image = f"data:{mime};base64,{b64_data}"

        status_code, resp_data = GROK_CLIENT.edit_image(prompt, image, aspect_ratio)
        if status_code != 200:
            self._send_json(status_code, resp_data)
            return

        # 保存编辑后的图片
        data_list = resp_data.get("data", [])
        host = self.headers.get("Host", f"127.0.0.1:{CONFIG['server']['port']}")

        for idx, item in enumerate(data_list):
            b64_str = item.get("b64_json")
            if b64_str:
                img_bytes = base64.b64decode(b64_str)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"edit_{timestamp}_{idx + 1}.jpg"
                filepath = IMAGES_DIR / filename
                filepath.write_bytes(img_bytes)

                local_url = f"http://{host}/outputs/images/{filename}"
                item["url"] = local_url
                item["local_path"] = str(filepath)

        self._send_json(200, resp_data)

    def _handle_video_generation(self, body):
        if not body.get("prompt"):
            self._send_json(400, {"error": "Missing required field: prompt"})
            return

        # 转换可能存在的本地文件为 Base64
        self._resolve_local_image_references(body)

        status_code, resp_data = GROK_CLIENT.start_video(body)
        self._send_json(status_code, resp_data)

    def _handle_poll_video(self, request_id):
        status_code, resp_data = GROK_CLIENT.poll_video(request_id)
        if status_code != 200:
            self._send_json(status_code, resp_data)
            return

        status = resp_data.get("status")
        video_info = resp_data.get("video", {})
        remote_url = video_info.get("url") if isinstance(video_info, dict) else None

        # 如果已经生成完成且有远端下载链接，尝试在后台下载至本地
        if status in ["done", "completed"] and remote_url:
            host = self.headers.get("Host", f"127.0.0.1:{CONFIG['server']['port']}")
            filename = f"vid_{request_id}.mp4"
            local_file = VIDEOS_DIR / filename
            if not local_file.exists():
                try:
                    print(f"[*] 正在下载生成的视频: {remote_url} -> {local_file.name}")
                    GROK_CLIENT.download_file(remote_url, local_file, timeout=60)
                except Exception as e:
                    print(f"[*] 下载视频失败: {e}")

            if local_file.exists():
                resp_data["local_url"] = f"http://{host}/outputs/videos/{filename}"
                resp_data["local_path"] = str(local_file)
            else:
                resp_data["local_url"] = remote_url

        self._send_json(200, resp_data)

    def _handle_video_sync(self, body):
        """同步等待模式：提交任务 -> 循环轮询 -> 下载 MP4 -> 返回结果"""
        if not body.get("prompt"):
            self._send_json(400, {"error": "Missing required field: prompt"})
            return

        self._resolve_local_image_references(body)

        print(f"[Video Sync] 提交视频生成任务: {body.get('prompt')[:50]}...")
        status_code, start_resp = GROK_CLIENT.start_video(body)
        if status_code != 200:
            self._send_json(status_code, start_resp)
            return

        request_id = start_resp.get("request_id")
        if not request_id:
            self._send_json(500, {"error": "未获取到 request_id", "raw": start_resp})
            return

        print(f"[Video Sync] 任务已启动, request_id: {request_id}，正在轮询进度...")
        max_wait_secs = int(body.get("max_wait_seconds", 300))
        poll_interval = 3
        start_time = time.time()

        while time.time() - start_time < max_wait_secs:
            time.sleep(poll_interval)
            sc, poll_resp = GROK_CLIENT.poll_video(request_id)
            if sc not in [200, 202]:
                print(f"[Video Sync] 轮询异常 HTTP {sc}: {poll_resp}")
                continue

            status = poll_resp.get("status")
            progress = poll_resp.get("progress", "")
            print(f"[Video Sync] 轮询中 (耗时 {int(time.time() - start_time)}s), 状态: {status}, 进度: {progress}%")

            if status in ["done", "completed"]:
                video_info = poll_resp.get("video", {})
                remote_url = video_info.get("url") if isinstance(video_info, dict) else None
                host = self.headers.get("Host", f"127.0.0.1:{CONFIG['server']['port']}")
                filename = f"vid_{request_id}.mp4"
                local_file = VIDEOS_DIR / filename

                if remote_url:
                    print(f"[*] 视频生成成功，正在拉取到本地: {local_file.name}")
                    try:
                        GROK_CLIENT.download_file(remote_url, local_file, timeout=60)
                        poll_resp["local_url"] = f"http://{host}/outputs/videos/{filename}"
                        poll_resp["local_path"] = str(local_file)
                    except Exception as e:
                        print(f"[*] 本地缓存下载遇到异常: {e}")
                        poll_resp["download_error"] = str(e)
                        poll_resp["local_url"] = remote_url

                self._send_json(200, poll_resp)
                return

            if status in ["failed", "expired", "error"]:
                self._send_json(500, {"error": f"视频生成失败: {status}", "raw": poll_resp})
                return

        self._send_json(504, {
            "error": f"视频生成等待超时 (超过 {max_wait_secs}s)",
            "request_id": request_id,
            "status": "timeout"
        })

    def _resolve_local_image_references(self, body):
        """如果参数中有本地文件路径，转为 base64 data url"""
        for key in ["first_frame", "last_frame"]:
            val = body.get(key)
            if val and isinstance(val, str) and Path(val).is_file():
                p = Path(val)
                mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
                b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                body[key] = f"data:{mime};base64,{b64}"

        # 检查 images 数组
        if "images" in body and isinstance(body["images"], list):
            new_imgs = []
            for item in body["images"]:
                if isinstance(item, str) and Path(item).is_file():
                    p = Path(item)
                    mime = mimetypes.guess_type(p.name)[0] or "image/jpeg"
                    b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                    new_imgs.append(f"data:{mime};base64,{b64}")
                else:
                    new_imgs.append(item)
            body["images"] = new_imgs

    def _handle_history(self):
        """返回已生成的图片和视频历史"""
        host = self.headers.get("Host", f"127.0.0.1:{CONFIG['server']['port']}")
        images = []
        for p in sorted(IMAGES_DIR.glob("*.jpg"), key=lambda x: x.stat().st_mtime, reverse=True):
            images.append({
                "filename": p.name,
                "url": f"http://{host}/outputs/images/{p.name}",
                "size_bytes": p.stat().st_size,
                "created_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat()
            })

        videos = []
        for p in sorted(VIDEOS_DIR.glob("*.mp4"), key=lambda x: x.stat().st_mtime, reverse=True):
            videos.append({
                "filename": p.name,
                "url": f"http://{host}/outputs/videos/{p.name}",
                "size_bytes": p.stat().st_size,
                "created_at": datetime.fromtimestamp(p.stat().st_mtime).isoformat()
            })

        self._send_json(200, {"images": images, "videos": videos})


def run_server():
    host = CONFIG["server"].get("host", "0.0.0.0")
    port = CONFIG["server"].get("port", 8000)

    print("=" * 60)
    print(" [*] Grok Media Service 启动中...")
    print(f" 本地服务地址: http://127.0.0.1:{port}")
    print(f" 局域网访问:   http://{host}:{port}")
    print(f" Web 演示界面: http://127.0.0.1:{port}/")
    print(f" 网络代理:     {CONFIG.get('proxy') or '使用系统默认'}")
    print("=" * 60)

    server = ThreadingHTTPServer((host, port), GrokMediaHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[Service] 服务已停止")
        server.server_close()


if __name__ == "__main__":
    run_server()
