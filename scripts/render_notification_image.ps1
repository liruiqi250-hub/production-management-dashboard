param([Parameter(Mandatory=$true)][string]$InputPath,[Parameter(Mandatory=$true)][string]$OutputPath)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Drawing
$model=Get-Content -LiteralPath $InputPath -Raw -Encoding UTF8 | ConvertFrom-Json
$bitmap=New-Object System.Drawing.Bitmap 1080,1280
$graphics=[System.Drawing.Graphics]::FromImage($bitmap)
$graphics.SmoothingMode='AntiAlias'
$graphics.TextRenderingHint='AntiAliasGridFit'
$fonts=@()
$brushes=@()
function Brush([string]$hex) {
  $b=New-Object System.Drawing.SolidBrush ([System.Drawing.ColorTranslator]::FromHtml($hex))
  $script:brushes+=,$b
  return $b
}
function Font([single]$size,[bool]$bold=$false) {
  $style=if($bold){[System.Drawing.FontStyle]::Bold}else{[System.Drawing.FontStyle]::Regular}
  $f=New-Object System.Drawing.Font 'Microsoft YaHei',$size,$style,([System.Drawing.GraphicsUnit]::Pixel)
  $script:fonts+=,$f
  return $f
}
function Text([string]$value,[single]$x,[single]$y,[single]$width,[single]$height,$font,$brush) {
  $rect=New-Object System.Drawing.RectangleF $x,$y,$width,$height
  $format=New-Object System.Drawing.StringFormat
  $format.Trimming=[System.Drawing.StringTrimming]::EllipsisCharacter
  $graphics.DrawString($value,$font,$brush,$rect,$format)
  $format.Dispose()
}
try {
  $graphics.Clear([System.Drawing.ColorTranslator]::FromHtml('#F7F9FC'))
  $ink=Brush '#172637';$secondary=Brush '#42576C';$blue=Brush '#235CB8';$amber=Brush '#8C4C0B';$white=Brush '#FFFFFF';$rule=Brush '#DCE4ED'
  $heading=Font 47 $true;$body=Font 34;$small=Font 29;$number=Font 76 $true;$label=Font 31;$pointFont=Font 35
  Text $model.title 64 50 952 115 $heading $ink
  Text $model.source 64 170 952 90 $body $secondary
  $graphics.FillRectangle($white,64,290,952,165)
  for($i=0;$i -lt 3;$i++) {
    $x=84+$i*317
    Text $model.metrics[$i].value $x 296 285 92 $number $blue
    Text $model.metrics[$i].label $x 396 285 46 $label $secondary
  }
  Text $model.urgent 64 480 952 50 $body $amber
  Text '需要跟进的重点' 64 558 952 55 (Font 38 $true) $ink
  $points=@($model.points)
  if($points.Count -eq 0) { Text '当前没有已登记的重点事项。' 64 630 952 180 $pointFont $secondary }
  for($i=0;$i -lt [Math]::Min(3,$points.Count);$i++) {
    $y=630+$i*145
    Text $points[$i] 64 $y 952 132 $pointFont $ink
    $graphics.FillRectangle($rule,64,($y+137),952,1)
  }
  if($model.workload) { Text $model.workload 64 1090 952 108 $small $secondary }
  Text $model.footer 64 1212 952 50 (Font 25) $secondary
  $bitmap.Save($OutputPath,[System.Drawing.Imaging.ImageFormat]::Png)
} finally {
  foreach($font in $fonts){$font.Dispose()}
  foreach($brush in $brushes){$brush.Dispose()}
  $graphics.Dispose();$bitmap.Dispose()
}
