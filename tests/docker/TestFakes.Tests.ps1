# Docker-level checks of the test fakes: no production image holds tests/fakes, and compose.test.yaml touches nothing of a
# real installation. Builds the images of compose.yaml, which Docker's cache makes quick after the first time.

BeforeAll {
    Set-StrictMode -Version Latest
    $script:RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    # Names that exist only in tests/fakes and in the image built from it.
    $script:FakePattern = '(^|/)fakes(/|$)|fake_(kobold|comfy|docker|nvml)|scripted_server'

    function Invoke-Docker {
        # Runs Docker and returns its output lines. Throws when it exits with a status other than 0.
        param([string[]]$Arguments)
        $output = & docker @Arguments
        if ($LASTEXITCODE -ne 0) { throw "docker $($Arguments -join ' ') failed with exit status $LASTEXITCODE" }
        return @($output)
    }

    function Get-ServiceSetting {
        # Returns the items of the setting Name of a service in the resolved configuration, or none when it lacks it.
        param($Service, [string]$Name)
        $property = $Service.PSObject.Properties[$Name]
        if ($null -eq $property) { return @() }
        return @($property.Value)
    }

    function Get-ImageFile {
        # Returns the path of every entry in the file system of Image, listed from an exported container that never runs.
        param([string]$Image)
        $container = (Invoke-Docker @('create', $Image))[0]
        $archive = Join-Path $TestDrive ("$($container.Substring(0, 12)).tar")
        try {
            $null = Invoke-Docker @('export', '--output', $archive, $container)
            $names = @(& tar -tf $archive)
            if ($LASTEXITCODE -ne 0) { throw "tar could not list the export of $Image" }
            return $names
        }
        finally {
            $null = & docker rm --force $container
        }
    }

    Push-Location $script:RepositoryRoot
    try {
        $null = Invoke-Docker @('compose', '-f', 'compose.yaml', 'build', '--quiet')
        $null = Invoke-Docker @('build', '--quiet', '--file', 'tests/fakes/Dockerfile', '--tag', 'lyrveil/fakes:test', '.')
        $script:TestProject = (Invoke-Docker @('compose', '-f', 'compose.test.yaml', 'config', '--format', 'json')) -join "`n" | ConvertFrom-Json
    }
    finally {
        Pop-Location
    }
}

Describe 'Production images' {
    It 'holds nothing of tests/fakes in <Image>' -ForEach @(
        @{ Image = 'lyrveil/frontend:local' }
        @{ Image = 'lyrveil/backend:local' }
        @{ Image = 'lyrveil/orchestrator:local' }
    ) {
        $files = @(Get-ImageFile -Image $Image)
        $files.Count | Should -BeGreaterThan 100 -Because 'an empty listing would pass for the wrong reason'
        @($files | Where-Object { $_ -match $script:FakePattern }) | Should -BeNullOrEmpty
    }

    It 'sees the fakes in the image built from tests/fakes/Dockerfile, so the check can fail' {
        $files = @(Get-ImageFile -Image 'lyrveil/fakes:test')
        @($files | Where-Object { $_ -match $script:FakePattern }).Count | Should -BeGreaterThan 3
    }
}

Describe 'compose.test.yaml' {
    It 'has its own project name' {
        $script:TestProject.name | Should -Be 'lyrveil-test'
    }

    It 'uses no volume of an installation and none that Compose does not own' {
        $volumes = @($script:TestProject.volumes.PSObject.Properties)
        $volumes.Count | Should -BeGreaterThan 0
        foreach ($volume in $volumes) {
            $volume.Value.name | Should -BeLike 'lyrveil-test_*'
            $volume.Value.PSObject.Properties.Name | Should -Not -Contain 'external'
        }
    }

    It 'names no container, so none collides with a real one' {
        foreach ($service in $script:TestProject.services.PSObject.Properties) {
            @(Get-ServiceSetting -Service $service.Value -Name 'container_name') | Should -BeNullOrEmpty
        }
    }

    It 'mounts no Docker socket and no host folder, asks for no GPU, and tags every image :test' {
        foreach ($service in $script:TestProject.services.PSObject.Properties) {
            foreach ($mount in @(Get-ServiceSetting -Service $service.Value -Name 'volumes')) {
                $mount.type | Should -Be 'volume' -Because "$($service.Name) may use only volumes owned by the project"
            }
            @(Get-ServiceSetting -Service $service.Value -Name 'gpus') | Should -BeNullOrEmpty
            $service.Value.image | Should -BeLike '*:test'
        }
    }

    It 'publishes one port, on 127.0.0.1 and not 4200' {
        $ports = @($script:TestProject.services.PSObject.Properties | ForEach-Object { Get-ServiceSetting -Service $_.Value -Name 'ports' })
        $ports.Count | Should -Be 1
        $ports[0].host_ip | Should -Be '127.0.0.1'
        $ports[0].published | Should -Not -Be '4200'
    }

    It 'has a fake in place of each runtime' {
        $names = @($script:TestProject.services.PSObject.Properties.Name)
        $names | Should -Contain 'lyrveil-llm'
        $names | Should -Contain 'lyrveil-image'
        $script:TestProject.services.'lyrveil-llm'.image | Should -Be 'lyrveil/fakes:test'
        $script:TestProject.services.'lyrveil-image'.image | Should -Be 'lyrveil/fakes:test'
    }
}
