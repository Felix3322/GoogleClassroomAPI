# GoogleClassroomAPI

按老师的思路操作 Google Classroom:课程、学生、主题、作业、缺交作业的默认成绩。
**只用老师浏览器里导出的 Cookie**——不需要 OAuth 客户端,不需要 Google Cloud 项目,运行时也不需要浏览器。
Google 网页端内部的协议(RPC 编号、位置数组、字段掩码、会话参数)都封装在库里。

> **非官方。** 本库调用的是 Google Classroom 网页应用自己使用的私有接口,是逆向得到的。
> Google 没有为它们提供文档,也不承诺兼容,随时可能失效;自动化调用也可能不符合 Google 的服务条款。
> 请先用测试账号和测试班级,风险自担。本项目与 Google 无关。

## 快速上手

```python
from google_classroom_api import Classroom

cls = Classroom.from_cookie_file("cookies.txt")

# 课程 id 就在 Classroom 的任何课程链接里(base64 编码)
course = Classroom.course_id_from_url("https://classroom.google.com/u/0/c/MTIzNDU2Nzg5MDEy")  # -> 123456789012

cls.course(course)        # {"course_id": "...", "name": "七年级数学", "section": "G7"}
cls.students(course)      # [{"user_id": "...", "name": "...", "email": "...", "photo_url": "..."}]

a = cls.create_assignment(
    course, "第 12 课 分数",
    instructions="完成表单。", points=10, topic="第一单元",
    form_url="https://docs.google.com/forms/d/e/.../viewform",   # 附上一份 Google 表单(可选)
    publish=True,                                                # False = 只存草稿,学生看不到
)
cls.assignments(course)   # [{"item_id", "title", "state": 2 已发布 / 3 草稿, "topic_id"}]
cls.update_assignment(course, int(a["item_id"]), "第 12 课 分数(修订)", publish=True)
cls.delete_assignment(course, int(a["item_id"]))

cls.missing_work_grade(course)                                 # {"enabled": True, "percent": 0.0}
cls.set_missing_work_grade(course, enabled=True, percent=50)   # 缺交作业自动给 50%
```

## Cookie 从哪来

用仓库里的 Chrome 扩展 `tools/cookie-export/` 一键导出(说明见该目录的 README):
在已登录老师账号的 Chrome 里点一下,得到 Netscape 格式的 `cookies.txt`。

**这个文件等于账号的完整登录状态,请当密码保管**,用完删除;不要提交进仓库(`.gitignore` 已排除)。
想让它彻底失效,在浏览器里退出该账号即可。

**导出的 Cookie 会自己过期。** 实测一份导出的 Cookie 用了大约两个小时后失效,而当时浏览器里的账号仍是登录状态
(浏览器会定期轮换会话令牌,导出的快照跟不上)。失效后库会抛出 `ReauthRequired`,重新导出一份即可。

## 能做什么(逐项标注验证状态)

“真机验证”指:用导出的 Cookie、在浏览器之外,对真实的 Google Classroom 服务器发请求并读回结果确认。

| 能力 | 方法 | 状态 |
|---|---|---|
| 从链接取课程 id | `Classroom.course_id_from_url` | 真机验证 |
| 课程名称、班级段 | `course` | 真机验证 |
| 花名册(学生 id、姓名、邮箱) | `students` | 真机验证 |
| 列出 / 新建主题(按名字复用) | `topics` / `ensure_topic` | 真机验证 |
| 新建作业(整班)、草稿 / 发布、主题、截止日期 | `create_assignment` | 真机验证 |
| 附 Google 表单 | `create_assignment(form_url=...)` | 已在网页端观察,尚未单独重放 |
| 改写作业 | `update_assignment` | 真机验证 |
| 列出全部作业和草稿(按主题分区) | `assignments` | 真机验证 |
| 删除作业 / 草稿 | `delete_assignment` | 真机验证 |
| 缺交作业默认成绩 开 / 关 / 百分比 | `missing_work_grade` / `set_missing_work_grade` | 真机验证 |
| 学生提交记录 | `submissions_raw` | 请求已真机验证;**有数据时的记录格式未验证**,返回原始数据 |
| **只发给指定学生** | `create_assignment(student_ids=...)` | **未完成**,调用会报错而不会误发给全班 |

## 还没做的,以及原因

- **只发给指定学生。** Classroom 网页端只有在班里至少有两个学生时才能表达"只发给其中几个";
  测试班只有一个学生,这个请求还没抓到。需要一个有两名以上学生的测试班。
- **学生提交记录的具体格式。** 测试班的学生还没打开或提交过作业,服务器只返回过空结果。
  需要有学生实际交过作业,才能确定字段。
- **列出老师的全部课程。** 网页端在启动时一次性加载课程列表,抓不到这个请求;请用 `course_id_from_url` 从链接取 id。
- **会话自动续期。** 浏览器会定期换新的会话令牌来保持登录,库还不会;长时间运行的服务需要定期重新导出 Cookie。
- **Google 表单。** Google 把 Docs/Forms 的编辑会话绑定在设备上,导出的 Cookie 在浏览器之外无法编辑表单
  (Classroom 不受此限)。表单部分以后补;仓库里 `forms.py` / `formbuilder.py` / `oauth_backends.py`
  是为此保留的官方 API 代码,当前对外接口不依赖它们。

协议细节和每一项的验证记录见 [`docs/private-classroom-protocol.md`](docs/private-classroom-protocol.md)。

## 测试

```bash
pip install -e .[dev]
pytest                                   # 离线测试
```

离线测试只做两件事:把库生成的请求和**真实抓到的请求**逐字比对;把**真实服务器返回过的响应**喂给解析器。
没有自己编写的模拟服务器,所以它们能防止请求被改坏,但证明不了 Google 今天仍然这样工作——那要靠真机测试:

```bash
# Windows PowerShell 用 $env:GCA_COOKIE_FILE="..." 设置
GCA_COOKIE_FILE=cookies.txt GCA_LIVE_COURSE_ID=<测试班的课程 id> pytest tests/live -k TestClassroom -v
```

真机测试会在测试班里新建并删除草稿、读写缺交成绩设置后恢复原值;测试班需要至少一名学生。

## 许可

MIT,见 [LICENSE](LICENSE)。
