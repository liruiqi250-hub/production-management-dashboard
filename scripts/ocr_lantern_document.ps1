param([Parameter(Mandatory=$true)][string]$InputPath,[Parameter(Mandatory=$true)][string]$OutputPath,[string]$PageIndices='')
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.InMemoryRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
$null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
$null=[Windows.Data.Pdf.PdfDocument,Windows.Data.Pdf,ContentType=WindowsRuntime]
$asyncMethod=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 } | Select-Object -First 1
$actionMethod=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and -not $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' } | Select-Object -First 1
function Await-Result($operation,[Type]$returnType) {
  $task=$asyncMethod.MakeGenericMethod($returnType).Invoke($null,@($operation));$task.Wait();return $task.Result
}
function Await-Action($operation) { $task=$actionMethod.Invoke($null,@($operation));$task.Wait() }
$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if (-not $engine) { throw '本机Windows文字识别语言组件未就绪' }
$maxDimension=[Windows.Media.Ocr.OcrEngine]::MaxImageDimension
function Read-ImageStream($stream,[int]$pageIndex) {
  $decoder=Await-Result ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
  $transform=New-Object Windows.Graphics.Imaging.BitmapTransform
  $ratio=[Math]::Min(1.0,$maxDimension/[Math]::Max($decoder.PixelWidth,$decoder.PixelHeight))
  $transform.ScaledWidth=[uint32][Math]::Max(1,[Math]::Floor($decoder.PixelWidth*$ratio))
  $transform.ScaledHeight=[uint32][Math]::Max(1,[Math]::Floor($decoder.PixelHeight*$ratio))
  $bitmap=Await-Result ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,[Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied,$transform,[Windows.Graphics.Imaging.ExifOrientationMode]::RespectExifOrientation,[Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
  try {
    $result=Await-Result ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
    $lines=@($result.Lines | ForEach-Object { @{text=$_.Text;words=@($_.Words | ForEach-Object { @{text=$_.Text;x=$_.BoundingRect.X;y=$_.BoundingRect.Y;width=$_.BoundingRect.Width;height=$_.BoundingRect.Height} })} })
    return @{page=$pageIndex;lines=$lines}
  } finally { $bitmap.Dispose() }
}
$file=Await-Result ([Windows.Storage.StorageFile]::GetFileFromPathAsync([IO.Path]::GetFullPath($InputPath))) ([Windows.Storage.StorageFile])
$pages=@()
if ([IO.Path]::GetExtension($InputPath).ToLowerInvariant() -eq '.pdf') {
  $pdf=Await-Result ([Windows.Data.Pdf.PdfDocument]::LoadFromFileAsync($file)) ([Windows.Data.Pdf.PdfDocument])
  if ($pdf.PageCount -gt 100) { throw 'PDF超过100页，本轮未自动识别，请拆分核对' }
  $indices=if ($PageIndices) { @($PageIndices.Split(',') | ForEach-Object { [uint32]$_ }) } else { @(0..($pdf.PageCount-1)) }
  foreach ($index in $indices) {
    $page=$pdf.GetPage($index);$stream=New-Object Windows.Storage.Streams.InMemoryRandomAccessStream
    try {
      $options=New-Object Windows.Data.Pdf.PdfPageRenderOptions
      $ratio=[Math]::Min(3.0,2000.0/[Math]::Max($page.Size.Width,$page.Size.Height))
      $options.DestinationWidth=[uint32][Math]::Floor($page.Size.Width*$ratio)
      $options.DestinationHeight=[uint32][Math]::Floor($page.Size.Height*$ratio)
      Await-Action ($page.RenderToStreamAsync($stream,$options));$stream.Seek(0)
      $pages+=Read-ImageStream $stream ([int]$index+1)
    } finally { $stream.Dispose();$page.Dispose() }
  }
} else {
  $stream=Await-Result ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
  try { $pages+=Read-ImageStream $stream 1 } finally { $stream.Dispose() }
}
@{language=$engine.RecognizerLanguage.LanguageTag;pages=@($pages)} | ConvertTo-Json -Depth 10 -Compress | Set-Content -LiteralPath $OutputPath -Encoding UTF8
