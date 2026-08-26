# 角域 · Blokus Online

支持 2–4 名玩家通过六位房间号加入的网页版角斗士棋。服务端使用 Python 标准库，无第三方运行依赖。

## 本地运行

```bash
python3 server/app.py --host 127.0.0.1 --port 4173
```

打开 <http://127.0.0.1:4173/>。

## 测试

```bash
python3 -m unittest discover -s tests -v
node --check game.js
python3 -m py_compile server/app.py server/game_engine.py
```

## 线上结构

- 静态文件：`/var/www/html/blokus`
- 房间服务：`127.0.0.1:8790`
- systemd：`blokus.service`
- Nginx 将 `/blokus/` 映射到静态文件，并将 `/blokus/api/` 代理到房间服务。

房间状态当前保存在服务进程内存中。服务重启后未结束的房间会失效；持久化和多实例扩展应在出现实际需求后加入。

部署脚本要求显式传入站点域名，仓库不保存具体环境的域名：

```bash
sudo python3 deployment/install.py --source . --domain example.com
python3 tests/public_smoke.py https://example.com/blokus/api
```

## 模式

- 在线模式：通过房间号加入 2–4 人房间。
- 离线模式：选择 2–4 人，一人轮流操作对应数量的颜色，不依赖服务器。
