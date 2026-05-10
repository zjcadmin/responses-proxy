# GitHub 发布清单 / GitHub Publishing Checklist

中文 | [English](#english)

## 中文

上传公开仓库前，请确认以下事项。

### 必须提交

- `app/`
- `scripts/`
- `tests/`
- `docs/`
- `packaging/`
- `.github/workflows/ci.yml`
- `.dockerignore`
- `.gitattributes`
- `.gitignore`
- `Dockerfile`
- `docker-compose.yml`
- `pyproject.toml`
- `README.md`
- `*.example.json`
- `.env.example`
- `start-*` / `stop-*` 脚本
- `build-desktop.*` 脚本

### 不要提交

- `.env`
- `manager-config.json`
- `model-config.json`
- `model-presets.json`
- `runtime/`
- `data/`
- `debug-*.json`
- `build/`
- `dist/`
- `*.egg-info/`
- `__pycache__/`
- `.venv/`

### 上传前检查

```bash
python -m pip install -e ".[dev]"
git status --short --ignored
python -m pytest -q
```

确认 `git status --short` 中没有真实 API Key、运行日志或打包产物。

### 发布二进制应用

不建议把 `dist/` 直接提交到源码仓库。推荐：

1. 在目标平台运行 `build-desktop.*`。
2. 把生成的 `.exe`、`.app` 或 Linux 可执行文件上传到 GitHub Releases。
3. 在 Release 描述里注明版本、平台、构建日期和默认访问地址。

### License

公开仓库如果需要别人合法复用代码，请补充你选择的 `LICENSE` 文件。未添加 License 时，即使仓库是公开的，默认也不代表别人有复用授权。

---

## English

Check the following items before publishing the repository.

### Commit These Files

- `app/`
- `scripts/`
- `tests/`
- `docs/`
- `packaging/`
- `.github/workflows/ci.yml`
- `.dockerignore`
- `.gitattributes`
- `.gitignore`
- `Dockerfile`
- `docker-compose.yml`
- `pyproject.toml`
- `README.md`
- `*.example.json`
- `.env.example`
- `start-*` / `stop-*` scripts
- `build-desktop.*` scripts

### Do Not Commit

- `.env`
- `manager-config.json`
- `model-config.json`
- `model-presets.json`
- `runtime/`
- `data/`
- `debug-*.json`
- `build/`
- `dist/`
- `*.egg-info/`
- `__pycache__/`
- `.venv/`

### Pre-Publish Checks

```bash
python -m pip install -e ".[dev]"
git status --short --ignored
python -m pytest -q
```

Make sure `git status --short` does not include real API keys, runtime logs, or packaged build artifacts.

### Publishing Binary Apps

Do not commit `dist/` directly to the source repository. Prefer this flow:

1. Run `build-desktop.*` on the target platform.
2. Upload the generated `.exe`, `.app`, or Linux executable to GitHub Releases.
3. Include version, platform, build date, and default local URLs in the release notes.

### License

If you want other people to legally reuse the code, add the `LICENSE` file you prefer. A public repository without a license does not automatically grant reuse rights.
