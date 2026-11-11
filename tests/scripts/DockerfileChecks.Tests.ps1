# Tests of scripts/lib/DockerfileChecks.psm1: an unpinned base image fails, stage references and scratch are skipped,
# and an ARG default used by FROM is resolved before the check.

BeforeAll {
    Set-StrictMode -Version Latest
    Import-Module (Join-Path $PSScriptRoot '..\..\scripts\lib\DockerfileChecks.psm1') -Force
    $script:Digest = 'sha256:' + ('a' * 64)
}

Describe 'Get-DockerfileBaseImage' {
    It 'accepts a tag with a digest' {
        $images = @(Get-DockerfileBaseImage -Text "FROM python:3.12-slim@$($script:Digest) AS base")
        $images.Length | Should -Be 1
        $images[0].Pinned | Should -BeTrue
    }

    It 'rejects a tag without a digest and a digest without a tag' {
        $text = "FROM python:3.12-slim`nFROM python@$($script:Digest)"
        $images = @(Get-DockerfileBaseImage -Text $text)
        $images.Length | Should -Be 2
        @($images | Where-Object { $_.Pinned }).Length | Should -Be 0
    }

    It 'skips scratch and earlier stages' {
        $text = "FROM python:3.12@$($script:Digest) AS base`nFROM base AS deps`nFROM scratch"
        @(Get-DockerfileBaseImage -Text $text).Length | Should -Be 1
    }

    It 'resolves an ARG default and flags it when unpinned' {
        $text = "ARG IMAGE=debian:trixie-slim`nFROM `${IMAGE} AS runtime"
        $images = @(Get-DockerfileBaseImage -Text $text)
        $images[0].Reference | Should -Be 'debian:trixie-slim'
        $images[0].Pinned | Should -BeFalse
    }
}

Describe 'Invoke-DockerfileCheck' {
    It 'passes on the repository Dockerfiles' {
        $root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
        $result = Invoke-DockerfileCheck -Root $root
        $result.Checked | Should -BeGreaterThan 0
        @($result.Failures) | Should -BeNullOrEmpty
    }
}
