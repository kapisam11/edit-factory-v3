# Windows maintenance scripts

These scripts are grouped here so the repository root stays focused on the README, launchers, package metadata, and standard GitHub project files.

## Verified backup

Run the backup utility from the repository root after starting Edit Factory:

```powershell
.\03-SIDE-CODE\scripts\windows\backup\BACKUP-EDIT-FACTORY.ps1 -Destination "E:\EditFactoryBackups"
```

The destination must be on a different drive from the repository. The script verifies the archive and performs an isolated restore drill. The archive is not encrypted; protect the destination drive with BitLocker or another appropriate disk-encryption mechanism.

## Scheduled publisher

The publisher queue runner and Windows Task Scheduler registration/removal scripts are in `publisher/`. Installing the task does not turn on network uploads; publication remains opt-in and should only be enabled after reviewing destination credentials, approvals, and account-specific acceptance tests.

```powershell
.\03-SIDE-CODE\scripts\windows\publisher\INSTALL-PUBLISHER-TASK.ps1
.\03-SIDE-CODE\scripts\windows\publisher\REMOVE-PUBLISHER-TASK.ps1
```
