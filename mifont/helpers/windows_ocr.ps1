param([Parameter(Mandatory=$true)][string]$ImagePath)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
    $null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
    $null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
    $null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Graphics.Imaging,ContentType=WindowsRuntime]
    $null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
    $null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
    $null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
    $taskMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    } | Select-Object -First 1
    function Await-Operation($Operation,[Type]$ResultType) {
        $asyncTask=$taskMethod.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
        return $asyncTask.GetAwaiter().GetResult()
    }
    $ocrLanguage=New-Object Windows.Globalization.Language('en-US')
    $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($ocrLanguage)
    if ($null -eq $engine) {$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()}
    if ($null -eq $engine) {throw 'No Windows OCR language is installed. Enter image text manually.'}
    $file=Await-Operation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($ImagePath)) ([Windows.Storage.StorageFile])
    $stream=Await-Operation ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder=Await-Operation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        $bitmap=Await-Operation ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,[Windows.Graphics.Imaging.BitmapAlphaMode]::Premultiplied)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            $recognized=Await-Operation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $words=@();$lineIndex=0
            foreach ($line in $recognized.Lines) {
                foreach ($word in $line.Words) {
                    $rect=$word.BoundingRect
                    $words+=@{text=$word.Text;box=@($rect.X,$rect.Y,$rect.Width,$rect.Height);line_id=$lineIndex}
                }
                $lineIndex++
            }
            @{available=$true;text=$recognized.Text;words=$words} | ConvertTo-Json -Depth 8 -Compress
        } finally {if ($null -ne $bitmap) {$bitmap.Dispose()}}
    } finally {if ($null -ne $stream) {$stream.Dispose()}}
} catch {
    @{available=$false;error=$_.Exception.Message;text='';words=@()} | ConvertTo-Json -Depth 3 -Compress
}
