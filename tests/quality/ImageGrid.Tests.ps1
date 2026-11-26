# T-QLT-02 and T-QLT-07: the image default grid for each image profile and the image identity protocol with its measured levers (PAG, identity weight, tall and wide sizes), rendered for real and written as a blind review sheet for the owner, in the project lyrveil-gpu-test.
#
# "scripts\lyrveil.cmd test quality ImageGrid" runs it on every catalog image profile, "... test quality ImageGrid
# illustrious-xl-v2.0" on that one, and "short" adds the smoke mode (one character, one scene and one seed, so every
# kind of render runs in minutes; it is no evidence for the catalog). Only the default image profile of the target
# tier also gets the identity protocol, the levers and the other sizes, so name it, or none, to run them.
# It builds the backend's test stage (Initialize-GpuBackendProbe), where backend/tests/gpu_probe/image_quality.py gives
# the graphs of every cell with the backend's own prompt and workflow builders, so this file copies no prompt, size or
# sampler value. For each profile it starts lyrveil-image alone, renders each cell, saves its image as a JPEG outside the
# container (Invoke-GpuImageGraph -SavePath), and removes the runtime. ImageQuality.psm1, which the unit level covers,
# turns the renders into the blind sheet and later computes every verdict.
# test-results/quality/image-<mode>-<time>/ holds sheet.md and images/ (for the owner), key.json (which profile, setting
# and lever made each image) and renders.json; once the owner's answers are imported with "scripts\lyrveil.cmd test
# quality-rating <sheet.md>", verdict.json. test-results/quality/image-<mode>-<time>.json, ignored by Git like the
# folder, holds the run's own record. The rendered images wait in a folder of the system's temporary files, which is
# removed at the end, so the results folder holds only the images under their opaque names.

# Pester passes Select, the items after the level in "test quality", to BeforeAll, which the analyzer does not follow.
[Diagnostics.CodeAnalysis.SuppressMessageAttribute('PSReviewUnusedParameter', 'Select', Justification = 'Read in BeforeAll.')]
param([string[]]$Select = @())

BeforeAll {
    Set-StrictMode -Version Latest
    $script:Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    Import-Module (Join-Path $script:Root 'scripts\lib\GpuLevel.psm1') -Force
    Import-Module (Join-Path $script:Root 'scripts\lib\ImageQuality.psm1') -Force
    Import-Module (Join-Path $script:Root 'scripts\lib\QualityLevel.psm1') -Force
    $script:Utf8 = New-Object System.Text.UTF8Encoding $false
    $script:Card = Get-GpuSample
    $script:BaselineMiB = $script:Card.UsedMiB
    $script:StartedAt = Get-Date

    $script:Catalog = [System.IO.File]::ReadAllText((Join-Path $script:Root 'config\model-catalog.json'), $script:Utf8) | ConvertFrom-Json
    $script:TextIds = [string[]]@(Get-QualityTextProfileId -Catalog $script:Catalog)
    $script:ImageIds = [string[]]@(Get-QualityImageProfileId -Catalog $script:Catalog)
    # The text profile ids that "test quality" passes to every file are accepted and ignored here.
    $script:Selection = Get-QualitySelection -Select $Select -TextProfileId $script:TextIds -ImageProfileId $script:ImageIds
    $script:Mode = $script:Selection.Mode.ToLowerInvariant()
    $stamp = $script:StartedAt.ToString('yyyyMMdd-HHmmss')
    $script:Folder = Join-Path $script:Root "test-results\quality\image-$script:Mode-$stamp"
    $script:ResultFile = Join-Path $script:Root "test-results\quality\image-$script:Mode-$stamp.json"
    $script:Staging = Join-Path ([System.IO.Path]::GetTempPath()) "lyrveil-image-quality-$([guid]::NewGuid().ToString('N'))"
    # The reference machine is of the target tier since ADR 0027; the lever cells go to that tier's default image profile.
    $script:Workload = [ordered]@{ tier = 'target'; start_timeout_s = 180; render_timeout_s = 300; release_limit_mib = $script:BaselineMiB + 1024 }
    $script:Sections = @('grid', 'identity', 'pag')
    $script:Record = [ordered]@{
        selection = [string[]]@($Select); mode = $script:Selection.Mode; profiles_selected = $script:Selection.ImageProfiles
        workload = $script:Workload; preparation = [ordered]@{}; profiles = [ordered]@{}
        sheet = [ordered]@{ folder = $script:Folder; written = $false }
    }
    $script:Problems = New-Object System.Collections.Generic.List[string]
    $script:Cells = New-Object System.Collections.Generic.List[object]
    $script:Planned = 0
    $script:DefaultProfile = ''

    function Get-Field {
        # Returns the property Name of Value, or $null when Value is $null or has no such property, under strict mode.
        param($Value, [string]$Name)
        if ($null -eq $Value) { return $null }
        $property = $Value.PSObject.Properties[$Name]
        if ($null -eq $property) { return $null }
        return $property.Value
    }

    function Get-ProfilePlan {
        # Returns the cells of the probe's plan for ProfileId, section by section, as records without a render yet, and
        # sets the default image profile it names. Adds a problem and returns what it could read when a plan failed.
        param([string]$ProfileId)
        $cells = New-Object System.Collections.Generic.List[object]
        foreach ($section in $script:Sections) {
            $answer = Invoke-GpuBackendProbe -Root $script:Root -Probe 'image_quality' -Action 'plan' -Body @{
                profile = $ProfileId; mode = $script:Mode; tier = $script:Workload.tier; section = $section
            }
            if ($answer.Status -ne 0 -or $null -eq $answer.Body) {
                $script:Problems.Add("The probe gave no $section plan for $ProfileId (exit $($answer.Status)).")
                continue
            }
            $script:DefaultProfile = [string](Get-Field $answer.Body 'default_profile')
            foreach ($cell in @(Get-Field $answer.Body 'cells')) {
                $cells.Add([ordered]@{
                        Id = [string]$cell.id; Section = [string]$cell.section; Variant = [string]$cell.variant; Profile = $ProfileId
                        Character = [string]$cell.character; Scene = [string]$cell.scene; Seed = [int]$cell.seed; Aspect = [string]$cell.aspect
                        Width = [int]$cell.width; Height = [int]$cell.height; Pair = ($cell.pair -eq $true); Expected = (Get-Field $cell 'expected')
                        Workflow = [string]$cell.workflow; Status = 'planned'; Seconds = -1.0; Path = ''
                    })
            }
        }
        return $cells.ToArray()
    }

    function Invoke-CellRender {
        # Renders Cell on the running lyrveil-image and saves its image under the staging folder. Sets Status to
        # 'success' only when the client ran to the end, ComfyUI reported success, exactly one image of the cell's size
        # was saved and the file was written; otherwise to the first thing that failed, and adds it to Problems.
        param($Cell, [int]$Index)
        $path = Join-Path $script:Staging ('cell-{0:D4}.jpg' -f $Index)
        $job = Invoke-GpuImageGraph -Root $script:Root -WorkflowJson $Cell.Workflow -TimeoutSecond $script:Workload.render_timeout_s -SavePath $path
        $result = $job.Result
        $Cell.Seconds = [double](Get-Field $result 'seconds')
        $status = [string](Get-Field $result 'status')
        if ($job.Status -ne 0) { $Cell.Status = "client exit $($job.Status)" }
        elseif ($status -ne 'success') { $Cell.Status = "ComfyUI status '$status'" }
        elseif ([int](Get-Field $result 'images') -ne 1) { $Cell.Status = "$([int](Get-Field $result 'images')) images saved" }
        elseif ([int](Get-Field $result 'width') -ne $Cell.Width -or [int](Get-Field $result 'height') -ne $Cell.Height) {
            $Cell.Status = "size $([int](Get-Field $result 'width')) x $([int](Get-Field $result 'height'))"
        }
        elseif ((Get-Field $result 'saved') -ne $true) { $Cell.Status = 'image not copied out' }
        else {
            $Cell.Status = 'success'
            $Cell.Path = $path
        }
        if ($Cell.Status -ne 'success') { $script:Problems.Add("$($Cell.Profile) $($Cell.Id): $($Cell.Status).") }
        Write-Host ("      {0} {1}: {2} in {3} s" -f $Cell.Profile, $Cell.Id, $Cell.Status, $Cell.Seconds)
    }

    function Invoke-ProfileRun {
        # Plans, starts lyrveil-image for, renders and removes the runtime of the catalog image profile ProfileId, and
        # returns its record. Every cell is kept in Cells, rendered or not, so a failure still leaves its reason.
        param([string]$ProfileId)
        $record = [ordered]@{ profile = $ProfileId; planned = 0; rendered = 0 }
        $cells = @(Get-ProfilePlan -ProfileId $ProfileId)
        $record.planned = $cells.Length
        $record.sections = [ordered]@{}
        foreach ($section in $script:Sections) { $record.sections[$section] = @($cells | Where-Object { $_.Section -eq $section }).Length }
        $script:Planned += $cells.Length
        Open-GpuCommandLog
        try {
            $record.released_before = Wait-GpuMemoryBelow -LimitMiB $script:Workload.release_limit_mib -TimeoutSecond 90
            if (-not $record.released_before) { $script:Problems.Add("The VRAM was not released before $ProfileId.") }
            $clock = [System.Diagnostics.Stopwatch]::StartNew()
            $record.up_status = Start-GpuTestRuntime -Root $script:Root -Service 'lyrveil-image' -TimeoutSecond $script:Workload.start_timeout_s
            $record.ready_seconds = [math]::Round($clock.Elapsed.TotalSeconds, 1)
            if ($record.up_status -ne 0) { $script:Problems.Add("lyrveil-image did not become healthy for $ProfileId (exit $($record.up_status)).") }
            else {
                $index = 0
                foreach ($cell in $cells) {
                    $index++
                    Invoke-CellRender -Cell $cell -Index (($script:Cells.Count) + $index)
                }
            }
        }
        finally {
            $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-image'
            $record.released_after = Wait-GpuMemoryBelow -LimitMiB $script:Workload.release_limit_mib -TimeoutSecond 90
            $record.commands = [string[]](Close-GpuCommandLog)
            foreach ($cell in $cells) { $script:Cells.Add($cell) }
        }
        if (-not $record.released_after) { $script:Problems.Add("The VRAM was not released after $ProfileId.") }
        $record.rendered = @($cells | Where-Object { $_.Status -eq 'success' }).Length
        return $record
    }

    $prep = $script:Record.preparation
    try {
        if ($script:Selection.Unknown.Length -gt 0) { throw "Unknown items: $($script:Selection.Unknown -join ', ')." }
        $null = New-Item -ItemType Directory -Path $script:Staging
        $prep.probe_built = Initialize-GpuBackendProbe -Root $script:Root
        if (-not $prep.probe_built) { throw 'The backend test stage did not build.' }
        foreach ($profileId in $script:Selection.ImageProfiles) {
            Write-Host "    T-QLT-02 and T-QLT-07 $($script:Selection.Mode) on ${profileId}"
            $script:Record.profiles[$profileId] = Invoke-ProfileRun -ProfileId $profileId
        }
    }
    catch { $script:Problems.Add("The protocol stopped: $($_.Exception.Message)") }

    # The blind sheet mixes the images of every profile selected, so the owner reviews without knowing the profile.
    try {
        if (@($script:Cells | Where-Object { $_.Status -eq 'success' }).Length -gt 0) {
            $blind = Get-ImageReviewSheet -Cell $script:Cells.ToArray() -DefaultProfile $script:DefaultProfile -Seed (Get-Random -Maximum 1000000) -Mode $script:Selection.Mode -Planned $script:Planned
            $paths = Write-ImageArtifact -Folder $script:Folder -Blind $blind -Cell $script:Cells.ToArray()
            $script:Record.sheet.written = $true
            $script:Record.sheet.path = $paths.Sheet
            $script:Record.sheet.evidence = $blind.Key.evidence
            $script:Record.sheet.items = $blind.Key.items.Count
            Write-Host "    Blind review sheet: $($paths.Sheet)"
        }
    }
    catch { $script:Problems.Add("The review sheet could not be written: $($_.Exception.Message)") }
}

AfterAll {
    $null = Remove-GpuTestRuntime -Root $script:Root -Service 'lyrveil-image'
    if (Test-Path -LiteralPath $script:Staging) { Remove-Item -LiteralPath $script:Staging -Recurse -Force }
    $script:Record.problems = $script:Problems.ToArray()
    $script:Record.default_profile = $script:DefaultProfile
    $script:Record.cells = @($script:Cells | ForEach-Object { [ordered]@{ id = $_.Id; profile = $_.Profile; status = $_.Status; seconds = $_.Seconds; width = $_.Width; height = $_.Height } })
    $folder = Split-Path -Parent $script:ResultFile
    if (-not (Test-Path -LiteralPath $folder)) { $null = New-Item -ItemType Directory -Path $folder }
    $document = [ordered]@{
        test         = 'T-QLT-02, T-QLT-07'
        file         = 'tests/quality/ImageGrid.Tests.ps1'
        started      = $script:StartedAt.ToString('yyyy-MM-ddTHH:mm:ssK')
        gpu          = $script:Card.Name
        total_mib    = $script:Card.TotalMiB
        baseline_mib = $script:BaselineMiB
        evidence     = $(if ($script:Selection.Mode -eq 'Full') { 'protocol' } else { 'smoke run only: the short mode is no evidence for the catalog' })
        run          = $script:Record
    }
    [System.IO.File]::WriteAllText($script:ResultFile, (ConvertTo-Json -InputObject $document -Depth 8), $script:Utf8)
    Write-Host "    Results: $script:ResultFile"
}

Describe 'T-QLT-02 and T-QLT-07: the image grid and the identity protocol' {
    It 'takes only short and catalog profiles' {
        $script:Selection.Unknown | Should -BeNullOrEmpty -Because 'ImageGrid takes short and catalog profile ids'
    }

    It 'builds the backend test stage that gives the graphs' {
        $script:Record.preparation.probe_built | Should -BeTrue
    }

    It 'renders every planned cell of every selected image profile' {
        $script:Problems | Should -BeNullOrEmpty
        foreach ($profileId in $script:Selection.ImageProfiles) {
            $entry = $script:Record.profiles[$profileId]
            $entry | Should -Not -BeNullOrEmpty -Because "$profileId must have run"
            $entry.planned | Should -BeGreaterThan 0 -Because "$profileId must have a plan"
            $entry.rendered | Should -Be $entry.planned -Because "every cell of $profileId must render and be saved"
            $entry.released_after | Should -BeTrue
        }
    }

    It 'plans the identity protocol and the levers on the default image profile when it is selected' {
        if ($script:Selection.ImageProfiles -cnotcontains $script:DefaultProfile) {
            Set-ItResult -Skipped -Because 'the default image profile is not part of this run, so the identity protocol and the levers were not planned'
            return
        }
        $entry = $script:Record.profiles[$script:DefaultProfile]
        $entry.sections.identity | Should -BeGreaterThan 0
        $entry.sections.pag | Should -BeGreaterThan 0
    }

    It 'writes a blind review sheet that names no profile' {
        $script:Record.sheet.written | Should -BeTrue -Because 'the sheet, key and images must be written'
        $sheet = [System.IO.File]::ReadAllText((Join-Path $script:Folder 'sheet.md'), $script:Utf8)
        foreach ($profileId in $script:Selection.ImageProfiles) {
            $sheet | Should -Not -Match ([regex]::Escape($profileId)) -Because 'the sheet is blind'
        }
        @(Get-ChildItem -LiteralPath (Join-Path $script:Folder 'images') -Filter '*.jpg').Length | Should -BeGreaterThan 0
    }
}
