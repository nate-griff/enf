$start = [datetime]"2026-04-30"
$end   = [datetime]"2026-05-10"

for ($d = $start; $d -le $end; $d = $d.AddDays(1)) {
    $date = $d.ToString("yyyy-MM-dd")
    .\.venv\Scripts\python.exe .\freqgauge_extract.py --input .\source_data\scraped_images\$date\ --output Z:\Nathan\FNetData\rawdata\$date.csv -j 10 --dedupe-ms 1000
}
