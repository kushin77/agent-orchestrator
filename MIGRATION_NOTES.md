# Migration Notes: agent-orchestrator

This repository was extracted from the elevatediq monorepo during Phase-2 dissection.

## Setup Instructions

### 1. Clone Repository
```bash
git clone https://github.com/kushin77/agent-orchestrator
cd agent-orchestrator
```

### 2. Install Dependencies

**For Go**:
```bash
# See Dockerfile for exact dependencies
go mod download
```

### 3. Run Tests
```bash
# Python
pytest -v

# Go
go test ./...
```

### 4. Build Docker Image
```bash
docker build -t kushin77/agent-orchestrator:latest .
```

### 5. Run Service
```bash
# Docker
docker run -p 8000:8000 kushin77/agent-orchestrator:latest

# Or directly (if dependencies installed)
go run cmd/main.go
```

## CI/CD Checklist

- [ ] Update `.github/workflows/ci.yml` with actual test commands
- [ ] Configure GitHub Actions secrets if needed
- [ ] Test locally before pushing
- [ ] Update Dockerfile with production settings
- [ ] Update requirements.txt or go.mod with final dependencies
- [ ] Add any missing environment variables to CI config
- [ ] Test Docker image build locally

## Source Mapping

Original location: `services/agent-orchestrator`  
Full monorepo: https://github.com/elevatediq-monorepo

## Next Steps

1. Follow the setup instructions above
2. Run tests locally to verify extraction was successful
3. Customize Dockerfile and CI/CD as needed for your deployment
4. Deploy using your CI/CD pipeline

