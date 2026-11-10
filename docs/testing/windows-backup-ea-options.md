# Windows backup options when extended attributes are present

The activation backup must be independently verified before replacing the running
build. The current cold-copy helper refuses NTFS extended attributes because its
Python copy does not preserve them. A separate user-EA prototype remains synthetic
only. The actual files' EA types have not been qualified; no real backup or restore
has been completed by this investigation.

| Route | Evidence and qualification still needed |
| --- | --- |
| BackupRead/BackupWrite | [Backup stream identifiers](https://learn.microsoft.com/en-us/windows/win32/api/winbase/ns-winbase-win32_stream_id) include EA data, but the generic documentation does not establish a kernel-EA round trip. A qualified implementation and isolated restore comparison are needed. |
| Robocopy | [Robocopy](https://learn.microsoft.com/en-us/windows-server/administration/windows-commands/robocopy) offers `/DCOPY:E` for directory EAs but can continue after directory EA copying fails. Success and file-body hashes alone do not verify all EAs. |
| DISM WIM `/EA` | [DISM](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/dism-image-management-command-line-options-s14?view=windows-11) explicitly skips `$Kernel.*` EAs. It cannot satisfy an all-EA contract where kernel EAs might be present. |
| CBS IT-reviewed volume image | A [VSS snapshot](https://learn.microsoft.com/en-us/windows-server/storage/file-server/volume-shadow-copy-service) with an appropriate volume-image tool is a candidate for qualification because it avoids reconstructing individual files through user-mode EA setters. This is an inference, not a verified guarantee; it needs appropriate privileges, independent storage, encryption/tool compatibility and an isolated restore test. A full volume may include credentials and unrelated data, so this expands the library-only scope and requires a separate choice. |

Microsoft documents that setting a [kernel EA](https://learn.microsoft.com/en-us/windows-hardware/drivers/ifs/kernel-extended-attributes)
from user mode can report success without changing it. No presented route should be
treated as verified solely because its command succeeds. Do not change source file
attributes or encryption to make a backup run.

The existing library and projects remain in place. Before any activation, obtain a
fresh idle/job and source inventory, select a qualified backup destination and method,
verify the copy and a recovery path, and preserve unresolved UCloud journals. Real
UCloud, Mac and NVivo acceptance remain separate.
