# Deployment & Usage Requirements for Client Organization

To deploy this application for multiple users within the organization, the client will need the following infrastructure and configuration.

## 1. Hosting Environment
Since multiple people need to access the app and its data, it cannot run on a single user's laptop.

### Option A: On-Premise Server (Recommended for Privacy)
- **Hardware**: A dedicated Windows or Linux server/VM within their internal network.
- **Network**: The server must strictly accept connections only from the internal LAN (Intranet). Ensure port `8001` (or port 80) is open in the firewall for internal traffic.
- **Python Environment**: Python 3.10+ installed on the server.

### Configuration (Optional)
By default, data is stored in the `data/` folder inside the application directory.
To store data elsewhere (e.g., on a mounted network drive or separate disk), set the environment variable:
- `INTEBO_DATA_DIR=/path/to/your/storage`

### Option B: Cloud Hosting (Azure/AWS)
- **Containerization**: Deploy via Docker (provides consistency).
- **Security**: Strict VNet/VPN rules to ensure only employees can access it.
- **Storage**: Persistent volume for the `data/` directory is critical (otherwise data is lost on restart).

## 2. Shared Data Storage
The application currently uses a local filesystem (`data/` folder) for database and file storage.

- **Concurrent Access**: The current `StateManager` uses in-memory caching with disk persistence. If multiple server instances (workers) are spawned (e.g., via gunicorn/uvicorn workers), they will have **inconsistent state**.
- **Critical Requirement**:
    - **Single Instance**: Run only **one process** of the application (`workers=1`).
    - **Backups**: Regular automated backups of the `data/` folder are essential.

## 3. Deployment Checklist
1.  **Server**: Provision a machine (e.g., Windows Server or Ubuntu).
2.  **Installation**:
    - Clone repo / Copy files.
    - `pip install -r requirements.txt`.
3.  **Service Setup**:
    - Configure it to run as a system service (Systemd on Linux, NSSM on Windows) so it auto-starts on reboot.
    - Command: `uvicorn app:app --host 0.0.0.0 --port 80` (runs on default web port).
4.  **Access**:
    - Users access via `http://<server-ip>/` or `http://<internal-dns-name>/`.

## 4. Limitations to Note
- **Concurrency**: The app is not designed for high-concurrency writes from hundreds of users simultaneously (due to JSON file locking/overwriting). It allows concurrent *reads* (viewing dashboard), but simultaneous *writes* (approving plans, toggling checkboxes) might have race conditions if not handled carefully (though the single-process model mitigates this).
- **Authentication**: There is **NO login system**. Anyone on the network who can reach the URL can view/edit everything. If this is a concern, basic auth (username/password) or SSO integration should be added.

## Summary for IT Dept
"We need a small internal VM/Server with Python 3.10+. It will run a single-process FastAPI web server on port 80. Data is stored in a local `./data` directory which requires daily backup. The app handles internal state management and does not require an external SQL database."
