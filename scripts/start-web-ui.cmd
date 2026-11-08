@echo off
setlocal
pushd "%~dp0.." || exit /b 1
set "PYTHONPATH=%CD%"
python -m web_content_fetcher.ui %*
set "_WCF_UI_EXIT=%errorlevel%"
popd
endlocal & exit /b %_WCF_UI_EXIT%
