# 待办桌宠

（动画做的有点烂，如果有兴趣可以自行修改绘画文件创建自己的桌宠）

一个运行在 Windows 10/11 桌面上的透明待办桌宠。它以置顶、无边框、透明窗口显示角色，通过系统托盘管理，不占用任务栏；你可以悬停查看任务、点击新增或聊天、拖动角色位置，并让任务和设置在重启后继续保留。

## 功能

- 透明无边框、始终置顶、可跨显示器拖动的桌宠窗口
- 系统托盘显示/隐藏、退出、开机自启和角色创意工坊
- 待办任务新增、编辑、删除、完成动画和稳定排序
- 截止时间、无截止时间、到期前黄色和过期红色提示
- 本地原子持久化，异常退出时保留上一份有效数据
- 睡眠、唤醒、待机和一次性互动动作
- 聊天模式：中文输入、`Enter` 发送、`Shift+Enter` 换行、`Esc` 或叉号退出
- 可选联网搜索：Tavily 优先，免费 Bing HTML 和 DuckDuckGo 公共源回退；搜索失败时继续使用模型已有知识回答
- `Ctrl+Shift+Z` 全局切换桌宠显示/隐藏
- 创意工坊角色包：替换透明 PNG 和 JSON 配置即可添加角色

## 直接使用

从 GitHub Releases 下载 `待办桌宠.exe`，双击即可运行，无需安装。程序只显示在 Windows 通知区域，不会出现在任务栏。

常用操作：

- 悬停角色：显示任务表
- 左键短按：打开“聊天/布置任务”选择面板
- 左键按住约 200ms 后移动：拖动桌宠
- 右键角色：打开隐藏或退出菜单
- 托盘双击或按 `Ctrl+Shift+Z`：显示/隐藏桌宠
- 聊天时按 `Enter` 发送，按 `Shift+Enter` 换行

数据默认保存在 `%APPDATA%\\深海待办桌宠`。API Key 使用 Windows DPAPI 加密，不会写入源码目录；不要把该目录中的配置文件上传到 GitHub。

## 聊天与联网搜索

首次进入聊天时，在“配置聊天 API”中填写 OpenAI 兼容接口地址、模型和 API Key。DeepSeek API 可以正常聊天，但 API 本身不提供浏览器权限。

开启联网搜索后，实时问题会按以下顺序尝试：

1. 已配置的 Tavily Search API
2. 无需 Key 的 Bing HTML 公共搜索
3. 无需 Key 的 DuckDuckGo 公共搜索

如果搜索源暂时不可用，程序仍会把问题交给聊天模型，要求它依据已有知识回答，并明确说明无法确认最新网络数据，不会伪造搜索结果。Tavily 有免费额度，Key 保存在用户目录并加密，不进入本仓库。

## 从源码运行

需要 Windows、Python 3.12 和 PySide6：

```powershell
python -m pip install -r requirements.txt
python main.py
```

运行测试：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m pytest -q
```

## 打包单文件 EXE

```powershell
python -m PyInstaller --noconfirm --clean .\\desktop_pet.spec
```

输出文件为 `dist\\待办桌宠.exe`。详细架构、测试、素材来源和角色扩展说明见 [`docs/`](docs/)。

## 项目结构

```text
app/       Qt 桌宠窗口、托盘、任务表、聊天和全局快捷键
core/      任务模型、持久化、排序、动画状态机和聊天搜索链路
assets/    角色配置、动画帧和应用图标
tests/     单元测试和 Qt 集成测试
docs/      架构、运行、测试、素材和角色扩展文档
```

## 素材与许可

角色素材来源和用途记录在 [`docs/素材来源.md`](docs/素材来源.md)。仓库不包含来源不明的网络图片。请在分发或制作新角色时确认素材许可，并在角色包目录中使用自己的透明 PNG 资源。
