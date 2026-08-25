# Secure Deployment Guide for Media Roulette

## Pre-Deployment Checklist

### 🔐 Security Requirements
- [ ] Generate strong SECRET_KEY (64+ characters)
- [ ] Generate SECURITY_PASSWORD_SALT (different from SECRET_KEY)
- [ ] Set strong admin password (16+ characters)
- [ ] Update ADMIN_EMAIL to valid address
- [ ] Configure SSL certificate via reverse proxy
- [ ] Test locally before deploying

## Local Development Setup

```bash
# Clone repository
git clone https://github.com/treefix50/media-roulette.git
cd media-roulette

# Create environment file
cp .env.example .env
nano .env  # Edit with your values

# Start locally
docker-compose -f docker-compose.yml up -d

# Access at http://localhost:8000
