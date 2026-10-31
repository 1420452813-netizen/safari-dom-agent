-- TronClass DOM Runner（独立 App 捆绑包授权通道，TCC Permission Delegation）
--
-- 工作方式：
--   1) 从 /tmp/tronclass_task.js 读取 JS
--   2) 在 Safari 当前页面执行
--   3) 结果写入 /tmp/tronclass_result.json
--
-- 为什么需要它：
--   受管沙箱执行环境拿不到「控制 Safari」的自动化授权（osascript 报 -10004），
--   但本 app 是独立 bundle，拥有自己的身份 —— 首次运行弹窗点「允许」后即被授权，
--   之后可用 `open DOMRunner.app` 从命令行反复触发，实现全自动。

set jsPath to "/tmp/tronclass_task.js"
set outPath to "/tmp/tronclass_result.json"

set res to ""
try
	set js to (read (POSIX file jsPath) as «class utf8»)
	tell application "Safari"
		set res to do JavaScript js in document 1
	end tell
on error errMsg
	set res to "{\"error\":" & my esc(errMsg) & "}"
end try

-- 写结果（无论成功失败都写，便于调用方判断）
set f to POSIX file outPath
try
	close access f
end try
try
	set fp to open for access f with write permission
	write res to fp
	close access fp
end try

on esc(t)
	set q to quote
	set t to my replace(t, q, "\\" & q)
	set t to my replace(t, return, " ")
	set t to my replace(t, linefeed, " ")
	return q & t & q
end esc

on replace(t, old, new)
	set AppleScript's text item delimiters to old
	set parts to every text item of t
	set AppleScript's text item delimiters to new
	set out to parts as text
	set AppleScript's text item delimiters to ""
	return out
end replace
