@echo off
REM HWK image tools launcher: sd-venv packages + project venv's CUDA torch.
REM Usage: run_image_tools.bat gen "a red fox in snow" --out D:\hwk-projects\images\fox.png
setlocal
set "SD_PY=D:\hwk-tools\sd-venv\Scripts\python.exe"
set "SD_SITE=D:\hwk-tools\sd-venv\Lib\site-packages"
set "TORCH_SITE=C:\Users\HmamK\OneDrive\Desktop\HWK-Aali-main\.venv\Lib\site-packages"
REM SD libs first (diffusers/transformers/hub), then project venv for CUDA torch.
REM sd-venv must NOT have torch installed, or its CPU wheel would shadow CUDA.
set "PYTHONPATH=%SD_SITE%;%TORCH_SITE%"
"%SD_PY%" "%~dp0image_tools.py" %*
endlocal
