# 深度优化大师1.0

自己弄的一个 Windows 系统优化/清理小工具，Flet 写的。不是公司项目，也没团队，代码基本手搓，自己用着还行就开源了。界面是暗色极简那套，拖拽用 Flet 原生，不搞 Qt 那些弯弯绕。

> **强烈建议 Win11 的电脑用，只推荐部分配置直接跑。老电脑、Win10 或以下别瞎点，碰底层出问题概不负责！**

这玩意儿不是玩具，会动系统底层：临时文件、系统日志、浏览器缓存、服务、组件、部分注册表项都在处理范围里。点之前先看日志和勾选项，别看见“一键优化”四个字就闭眼按。

## 怎么用

需要 Python 3.10+，我这边用的是 3.13。把仓库拉下来，或者下 ZIP 解压：

```bash
git clone https://github.com/HaoBaB521/Deep-Optimizer.git
cd Deep-Optimizer
python -m pip install -r requirements.txt
python FletMaster.py
```

想要 exe 就自己打，别指望仓库里塞几十 MB 的构建垃圾：

```powershell
flet pack FletMaster.py -D --name "深度优化大师1.0" --icon app.ico --add-data "assets:assets" --hidden-import psutil
```

打完在 `dist\深度优化大师1.0\`，双击 `深度优化大师1.0.exe` 就行。安装包用 `深度优化大师1.0.iss` 配 Inno Setup 7 编译。

## 说明

- 清理前先只读扫描，扫完才允许执行。
- 普通权限不够就点“以管理员重启”。
- 服务与组件页目前只读，不会真删服务。
- 回收站、休眠文件不是默认清理项。
- 桌面、文档、图片不会进清理队列。

## 免责

开源免费，瞎搞坏了系统自己重装，别来找我。

会碰系统底层，出问题自己兜着。别在公司生产机、服务器、别人电脑上试；先拿自己 Win11 机器玩。
