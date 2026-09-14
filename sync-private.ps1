# 把 main 的最新進度同步進 private branch，並推到 private-origin。
# 使用時機：main 有新的 commit、想讓 private branch 跟上時執行。
# 用法： .\sync-private.ps1

$status = git status --porcelain
if ($status) {
    Write-Error "工作目錄有未提交的變更，請先 commit 或 stash 再執行同步。"
    exit 1
}

$originalBranch = (git rev-parse --abbrev-ref HEAD).Trim()

git checkout private
if ($LASTEXITCODE -ne 0) {
    Write-Error "切換到 private branch 失敗。"
    exit 1
}

git merge main --no-edit
if ($LASTEXITCODE -ne 0) {
    Write-Error "merge main 時發生衝突，請手動解決衝突、commit 後，自行執行 'git push private-origin private'。"
    exit 1
}

git push private-origin private
if ($LASTEXITCODE -ne 0) {
    Write-Error "push 到 private-origin 失敗，請檢查網路或權限後重試（'git push private-origin private'）。"
    git checkout $originalBranch | Out-Null
    exit 1
}

git checkout $originalBranch | Out-Null
Write-Host "同步完成：main -> private，已推到 private-origin。目前在 $originalBranch。"
