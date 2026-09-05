param(
  [Parameter(Mandatory = $true)][string]$ImagePath
)
# OCR an image with the Windows built-in engine (Windows.Media.Ocr).
# Output: JSON array of { text, left, top, right, bottom } per recognized line.
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
Add-Type -AssemblyName System.Drawing

$null = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$null = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$null = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime]
$null = [Windows.Storage.Streams.RandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]

function Await($WinRtTask, $ResultType) {
  $asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
  })[0]
  $netTask = $asTaskGeneric.MakeGenericMethod($ResultType).Invoke($null, @($WinRtTask))
  $netTask.Wait(-1) | Out-Null
  $netTask.Result
}

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {
  Write-Output '[]'
  exit 1
}

$stream = [System.IO.File]::Open($ImagePath, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read)
$randomStream = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($stream)
$decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($randomStream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$softwareBitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$result = Await ($engine.RecognizeAsync($softwareBitmap)) ([Windows.Media.Ocr.OcrResult])

$lines = @()
foreach ($line in $result.Lines) {
  $left = [int]::MaxValue; $top = [int]::MaxValue; $right = 0; $bottom = 0
  foreach ($word in $line.Words) {
    $rect = $word.BoundingRect
    if ($rect.X -lt $left) { $left = [int]$rect.X }
    if ($rect.Y -lt $top) { $top = [int]$rect.Y }
    if (($rect.X + $rect.Width) -gt $right) { $right = [int]($rect.X + $rect.Width) }
    if (($rect.Y + $rect.Height) -gt $bottom) { $bottom = [int]($rect.Y + $rect.Height) }
  }
  $lines += [pscustomobject]@{
    text = $line.Text
    left = $left; top = $top; right = $right; bottom = $bottom
  }
}
$stream.Dispose()
$lines | ConvertTo-Json -Compress