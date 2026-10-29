# asstmgt_v_1.2
AssetPro Enterprise Hub is a Python/Flask asset management system running as a Windows desktop app or web portal. It tracks hardware lifecycles, straight-line depreciation, warranties, and check-in/out custodians using SQLite with strict RBAC, audit logging, and CSV import/export.


 AssetPro Enterprise Hub — Technical & System Documentation
1. Executive Summary
AssetPro Enterprise Hub is a lightweight, security-first asset lifecycle management solution designed to bridge the gap between fragile spreadsheets and complex enterprise software. It operates as both a standalone desktop application (packaged with `pywebview` and PyInstaller) and a web application capable of being deployed on Linux servers, cloud infrastructure, or cPanel/shared hosting.
 2. System Architecture & Tech Stack
 Backend Framework: Python (Flask 3.x), WSGI compliant
 Database Engine: SQLite3 with WAL mode and foreign key support
 Desktop Runtime: `pywebview` (utilizing Microsoft Edge WebView2 on Windows)
 Packaging & Binary Distribution: PyInstaller (desktop executable build target)
 Frontend: HTML5, CSS3, Feather Icons, Vanilla JavaScript
 Security & Auth: Password hashing via Werkzeug (`scrypt`), Role-Based Access Control (RBAC), anti-CSV/Formula injection sanitization, session security
 3. Database Schema
The database schema (`assets.db`) consists of five primary tables:
 3.1 `assets`
Stores hardware and facility assets with financial and depreciation tracking:
 `id`: INTEGER PRIMARY KEY AUTOINCREMENT
 `asset_tag`: TEXT UNIQUE NOT NULL
 `name`: TEXT NOT NULL
 `item_type`: TEXT
 `category`: TEXT NOT NULL DEFAULT 'General'
 `serial_number`: TEXT
 `purchase_date`: TEXT (ISO format `YYYY-MM-DD`)
 `purchase_cost`: REAL DEFAULT 0
 `supplier`: TEXT
 `warranty_expiry`: TEXT (ISO format `YYYY-MM-DD`)
 `location`: TEXT
 `assigned_to`: TEXT
 `status`: TEXT CHECK (`status` IN ('In Use', 'In Storage', 'Under Maintenance', 'Retired')) DEFAULT 'In Storage'
 `salvage_value`: REAL DEFAULT 0
 `useful_life_years`: INTEGER DEFAULT 0
 `depreciation_method`: TEXT DEFAULT 'straight-line'
 `created_at`: TIMESTAMP DEFAULT CURRENT_TIMESTAMP
 `updated_at`: TIMESTAMP DEFAULT CURRENT_TIMESTAMP

 3.2 `categories`
Maintains controlled taxonomy values:
 `id`: INTEGER PRIMARY KEY AUTOINCREMENT
 `name`: TEXT UNIQUE NOT NULL

 3.3 `locations`
Maintains official facilities and deployment locations:
 `id`: INTEGER PRIMARY KEY AUTOINCREMENT
 `name`: TEXT UNIQUE NOT NULL

 3.4 `users`
Enforces user authentication and Role-Based Access Control:

 `id`: INTEGER PRIMARY KEY AUTOINCREMENT
 `username`: TEXT UNIQUE NOT NULL
 `password_hash`: TEXT NOT NULL
 `full_name`: TEXT
 `role`: TEXT CHECK (`role` IN ('Administrator', 'Data_Entry', 'Auditor', 'Viewer')) DEFAULT 'Viewer'
 `created_at`: TIMESTAMP DEFAULT CURRENT_TIMESTAMP

 3.5 `audit_logs`
Chronological, tamper-evident security audit trail:
 `id`: INTEGER PRIMARY KEY AUTOINCREMENT
 `timestamp`: TIMESTAMP DEFAULT CURRENT_TIMESTAMP
 `username`: TEXT NOT NULL
 `action`: TEXT NOT NULL
 `details`: TEXT

 4. Role-Based Access Control (RBAC) Matrix
| Feature / Action | Administrator | Data Entry Officer | Auditor | Viewer |
|  |  |  |  |  |
| View Assets & Run Filters | Yes | Yes | Yes | Yes |
| Export Filtered Assets (CSV) | Yes | Yes | Yes | Yes |
| Register Asset (Manual Modal) | Yes | Yes | No | No |
| Bulk Import Assets (`assets_template.csv`) | Yes | Yes | No | No |
| Check-In / Check-Out Custodian Flow | Yes | Yes | No | No |
| Edit Existing Asset Records | Yes | Yes | No | No |
| Delete Asset Records | Yes | No | No | No |
| Manage Categories & Locations | Yes | Yes | No | No |
| View Security & Compliance Audit Logs | Yes | No | Yes | No |
| Create / Bulk Import User Accounts | Yes | No | No | No |
| One-Click Database Backup | Yes | No | No | No |

 5. Key System Features & Capabilities
 5.1 Dual Asset Ingestion Engine

 Manual Modal: Allows input across all 14 hardware, financial, and custodian attributes with searchable `<datalist>` dropdowns.
 Smart CSV Import: Maps column aliases (e.g., `Item Name` ➔ `name`, `Serial / Product ID` ➔ `serial_number`, `Assigned Employee` ➔ `assigned_to`), normalizes statuses (e.g., `In use` ➔ `In Use`), and auto-generates tags (`AST-XXXXXX`) if tags are omitted.

 5.2 Financial Depreciation & Warranty Telemetry
 Automatically calculates Annual Depreciation and Current Net Book Value using the straight-line model:
$$\text{Depreciable Base} = \max(0, \text{Purchase Cost} - \text{Salvage Value})$$
$$\text{Current Value} = \max(\text{Salvage Value}, \text{Purchase Cost} - (\text{Annual Depreciation} \times \text{Age}))$$
 Warranty Tracking: Real-time badge tagging categorized as Active, Expiring ($\le 60$ days), or Expired.

 5.3 Custodian Accountability (Check-In / Check-Out)
 Check-Out: Assigns equipment to an employee, records destination location, sets state to In Use, and logs the event.
 Check-In: Clears the assignee, transfers the device back to storage/warehouse, sets state to In Storage, and appends an audit record.

 5.4 Data Recovery & Export Tools
 Dynamic CSV Export: Generates an immediate UTF-8 CSV containing all currently filtered search results with financial valuations.
 Desktop-Safe Download Handler: Uses a backend filesystem write to the operating system's `Downloads` directory, resolving file-save issues in desktop wrappers while retaining standard browser download capability.
 One-Click SQLite Backup: Automatically creates a timestamped snapshot of `assets.db` in the user's `Downloads` folder.



 6. Setup, Packaging & Deployment
 6.1 Local Development Environment
bash
pip install Flask>=3.0.0 werkzeug>=3.0.0 pywebview>=5.0.0
python app.py

 6.2 Compiling the Desktop Executable (PyInstaller)
Run the following PowerShell command from the project root:
powershell
pyinstaller --noconfirm --onedir --windowed --add-data "templates;templates" --name "AssetManager" app.py
The compiled application output is located at `dist\AssetManager\AssetManager.exe`.

 6.3 Namecheap cPanel Web Deployment
1. Navigate to cPanel > Software > Setup Python App.
2. Create an application with Python 3.10+ and application root `asset_app`.
3. Configure `passenger_wsgi.py`:
python
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
from app import app as application
4. Upload `app.py`, `templates/`, and `requirements.txt`, then install requirements via the cPanel interface.
5. Define the `FLASK_SECRET_KEY` environment variable and restart the application.

 7. Next Phase: Roadmap & Future Enhancements

The next development phase focuses on field operations, regulatory compliance, hardware automation, and enterprise integrations.

 Phase 2 Implementation Roadmap
++
|                                 PHASE 2 ROADMAP                                 |
++
|                                                                                 |
|  [2.1 Barcode & QR Labeling]       [2.2 Handover Form Sign-Off]                 |
|   SVG/PNG QR generation            Auto-generated checkout PDF                |
|   Zebra/Avery label printing       Digital canvas signature capture           |
|                                                                                 |
|  [2.3 Automated Remote Backups]    [2.4 Preventive Maintenance]                 |
|   Scheduled sync to S3 / R2        Recurring inspection scheduling            |
|   Encrypted database dumps         Downtime & maintenance cost log            |
|                                                                                 |
|  [2.5 SSO & Active Directory]      [2.6 Mobile Scanner Web App]                 |
|   SAML 2.0 / OAuth2 (Azure AD)     Smartphone camera barcode audit            |
|   Automated employee sync          Rapid physical stock verification          |
|                                                                                 |

 7.1 Printable Barcode & QR Code Labels
 Feature: Automatic generation of standard Code-128 barcodes and 2D QR codes for every generated `asset_tag`.
 Deliverable: Integrated printable label sheet (compatible with standard thermal printers such as Zebra, Brother, and Avery label sheets) containing company branding, tag number, and serial number.

 7.2 Digital Handover & Acceptance Slips (PDF Sign-Off)
 Feature: Automated generation of an asset handover agreement whenever an asset is checked out to an employee.
 Deliverable:
 Server-side PDF generation incorporating employee details, device condition, and acceptable use policy terms.
 Digital signature capture via an HTML5 canvas during checkout.

 7.3 Automated Cloud Backup & Disaster Recovery
 Feature: Scheduled, zero-downtime backup snapshots sent to remote, off-site storage.
 Deliverable: Automated cron/worker utility that creates an SQLite VACUUM backup and securely uploads it to an Amazon S3 bucket, Cloudflare R2, or network-attached storage (NAS) with retention policies.

 7.4 Preventive Maintenance & Servicing Logs
 Feature: Granular maintenance tracking beyond a single status flag.
 Deliverable: A dedicated `maintenance_logs` table tracking service dates, vendor costs, part replacements, technician notes, and alerts for scheduled routine servicing.

 7.5 Enterprise Single Sign-On (SSO) & LDAP Integration
 Feature: Centralized identity management for larger organizations.
 Deliverable: Integration with Microsoft Entra ID (Azure AD), Google Workspace, or generic LDAP/SAML 2.0 to eliminate separate local user password management.

 7.6 Mobile Camera Stock Audit (PWA Scanner)
 Feature: Rapid physical verification during periodic inventory audits.
 Deliverable: A lightweight mobile-responsive scanning mode using standard smartphone camera feeds (`getUserMedia`) to scan QR codes, verify location and custodian, and flag missing assets in real time.
