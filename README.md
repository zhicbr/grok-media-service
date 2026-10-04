# Grok Media Service

基于本地 **Grok Build (SuperGrok)** 凭证的本地多媒体生成服务，提供兼容 OpenAI 规范的生图接口和完整的视频生成接口。

---

## 🌟 特性

1. **零第三方依赖**：纯 Python 3 原生标准库实现，无需 `pip install`，双击即跑；
2. **自动复用 Grok 登录态**：自动读取 `~/.grok/auth.json` 中的 SuperGrok 凭证，支持 OIDC Token 自动无感续期与写回；
3. **OpenAI 兼容协议**：提供标准 `/v1/images/generations` 接口，市面上绝大多数 AI 客户端（NextChat、LobeChat、Cherry Studio、Dify 等）开箱即用；
4. **视频生成“同步等待”模式**：官方视频接口为异步轮询，本项目额外封装 `/v1/videos/sync` 接口，调一次自动在后台轮询、下载 MP4 落盘并返回本地链接；
5. **本地文件自动落盘**：所有生成的图片与视频自动分类保存在 `outputs/images/` 和 `outputs/videos/`，并提供本地静态 HTTP 直链；
6. **自带精美 WebUI**：访问 `http://127.0.0.1:8000` 即可在浏览器可视化体验文本生图、图生图、视频生成以及历史作品库。

---

## 🚀 快速启动

### 方式一：命令行运行
在当前目录运行：
```bash
python server.py
```

### 方式二：Windows 双击启动
直接双击运行目录下的 `start.bat`。

启动成功后，浏览器打开：
👉 **http://127.0.0.1:8000/**

---

## ⚙️ 配置文件说明 (`config.json`)

```json
{
  "server": {
    "host": "0.0.0.0",
    "port": 8000
  },
  "proxy": "http://127.0.0.1:7890",
  "defaults": {
    "image": {
      "aspect_ratio": "16:9"
    },
    "video": {
      "duration": 6,
      "resolution_name": "480p",
      "aspect_ratio": "16:9"
    }
  },
  "storage": {
    "auto_save": true,
    "images_dir": "outputs/images",
    "videos_dir": "outputs/videos"
  },
  "auth": {
    "auto_read_grok_auth": true,
    "grok_auth_path": "~/.grok/auth.json",
    "auto_refresh_token": true
  }
}
```

* **网络代理**：如果系统使用 Clash / v2ray 等，确保 `proxy` 填写真实本地代理端口（如 `http://127.0.0.1:7890`）。

---

## 📡 API 接口规范

### 1. 检查凭据状态
* **请求**：`GET /v1/auth/status`
* **响应**：
  ```json
  {
    "status": "active",
    "email": "user@example.com",
    "expires_at": "2026-10-04T14:45:08.982250700Z",
    "remaining_seconds": 3600,
    "expired": false
  }
  ```

---

### 2. 文本生图 (OpenAI 兼容)
* **请求**：`POST /v1/images/generations`
* **Body**：
  ```json
  {
    "prompt": "一只在雨中霓虹街头穿风衣的猫咪，电影质感，写实高细节",
    "aspect_ratio": "16:9"
  }
  ```
* **响应**：
  ```json
  {
    "created": 1728000000,
    "data": [
      {
        "url": "http://127.0.0.1:8000/outputs/images/img_20261004_210000_1.jpg",
        "local_path": "D:\\...\\outputs\\images\\img_20261004_210000_1.jpg",
        "b64_json": "..."
      }
    ]
  }
  ```

---

### 3. 图生图 / 图像编辑
* **请求**：`POST /v1/images/edits`
* **Body**：
  ```json
  {
    "prompt": "保持主体人物动作，将背景切换为赛博朋克城市",
    "image": "D:\\photos\\portrait.jpg", 
    "aspect_ratio": "auto"
  }
  ```
  *(注：`image` 字段支持直接传入本地文件绝对路径、公网 URL 或 Base64 编码，服务端会自动解析)*

---

### 4. 视频生成 - 同步等待模式（推荐）
* **请求**：`POST /v1/videos/sync`
* **Body**：
  ```json
  {
    "prompt": "微风拂过绿油油的麦田，镜头平滑推进，阳光明媚，电影质感",
    "duration": 6,
    "resolution_name": "480p",
    "aspect_ratio": "16:9",
    "first_frame": "D:\\photos\\start_frame.jpg"
  }
  ```
* **响应**：服务端将自动提交任务、循环轮询，直到生成完毕后流式下载到本地硬盘并返回：
  ```json
  {
    "status": "completed",
    "local_url": "http://127.0.0.1:8000/outputs/videos/vid_vg-xxxxxx.mp4",
    "local_path": "D:\\...\\outputs\\videos\\vid_vg-xxxxxx.mp4"
  }
  ```

---

### 5. 视频生成 - 原生异步模式
1. 提交任务：`POST /v1/videos/generations` -> 返回 `{"request_id": "vg-xxxxxx"}`
2. 轮询状态：`GET /v1/videos/{request_id}` -> 返回生成进度，完成后自动下载并包含 `local_url`

---

## 🧪 客户端测试

在终端运行自带的测试脚本：
```bash
# 1. 测试状态检查
python test_client.py --mode status

# 2. 测试生成一张图片
python test_client.py --mode image --prompt "一只正在弹吉他的小熊猫"

# 3. 测试生成一个视频
python test_client.py --mode video --prompt "海浪拍打着岩石，慢动作摄影"
```
