# Contributing to AI Test Studio

Thank you for your interest in contributing to AI Test Studio! This document provides guidelines and instructions for contributing.

## Table of Contents

- [Code of Conduct](#code-of-conduct)
- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Contributing Process](#contributing-process)
- [Code Style Guidelines](#code-style-guidelines)
- [Project Structure](#project-structure)
- [Testing](#testing)
- [Pull Request Process](#pull-request-process)
- [Reporting Issues](#reporting-issues)

---

## Code of Conduct

This project adheres to a code of conduct that all contributors are expected to follow. Please be respectful and constructive in all interactions.

---

## Getting Started

1. **Fork the repository** on GitHub
2. **Clone your fork** locally:
   ```bash
   git clone https://github.com/your-username/AI-Test-Studio.git
   cd AI-Test-Studio
   ```
3. **Add the upstream repository**:
   ```bash
   git remote add upstream https://github.com/original-owner/AI-Test-Studio.git
   ```

---

## Development Setup

### Prerequisites

- Python 3.9 or higher
- Git
- Ollama (for local LLM)
- Terminal/Command Prompt access

### Setup Steps

1. **Create a virtual environment**:
   ```bash
   python -m venv venv
   
   # Activate virtual environment
   # Windows:
   venv\Scripts\activate
   # macOS/Linux:
   source venv/bin/activate
   ```

2. **Install dependencies**:
   ```bash
   pip install --upgrade pip
   pip install -r requirements.txt
   ```

3. **Set up configuration**:
   ```bash
   # Copy environment template
   cp config/env.example config/.env
   
   # Edit config/.env with your settings
   # At minimum, set:
   # - SECRET_KEY (generate a secure key)
   ```

4. **Install and start Ollama** (if not already installed):
   ```bash
   # Follow instructions at https://ollama.ai
   # Pull default model:
   ollama pull llama3.2:3b
   ```

5. **Initialize storage directories**:
   ```bash
   # Run the storage initialization script
   bash scripts/init_storage.sh  # macOS/Linux
   # or
   scripts\init_storage.bat      # Windows
   ```

6. **Run the development server**:
   ```bash
   python backend/app.py
   ```

---

## Contributing Process

### 1. Create a Feature Branch

Always create a new branch for your work:

```bash
# Update your main branch
git checkout main
git pull upstream main

# Create a new branch
git checkout -b feature/your-feature-name
# or
git checkout -b fix/your-bug-fix
```

**Branch naming conventions:**
- `feature/` - New features
- `fix/` - Bug fixes
- `docs/` - Documentation updates
- `refactor/` - Code refactoring
- `test/` - Test additions/updates

### 2. Make Your Changes

- Write clear, readable code
- Follow the existing code style
- Add comments for complex logic
- Update documentation if needed
- Test your changes thoroughly

### 3. Commit Your Changes

Write clear, descriptive commit messages:

```bash
git add .
git commit -m "Add feature: description of what you added"
```

**Commit message format:**
- Use present tense ("Add feature" not "Added feature")
- Be specific and concise
- Reference issues if applicable: "Fix #123: description"

### 4. Push to Your Fork

```bash
git push origin feature/your-feature-name
```

### 5. Open a Pull Request

1. Go to the original repository on GitHub
2. Click "New Pull Request"
3. Select your branch
4. Fill out the PR template (if available)
5. Submit the PR

---

## Code Style Guidelines

### Python

- Follow [PEP 8](https://www.python.org/dev/peps/pep-0008/) style guide
- Use meaningful variable and function names
- Keep functions focused and small
- Add docstrings for classes and functions
- Maximum line length: 100 characters (soft limit)

**Example:**
```python
def process_document(file_path: str) -> dict:
    """
    Process a document and extract text content.
    
    Args:
        file_path: Path to the document file
        
    Returns:
        Dictionary containing extracted content and metadata
    """
    # Implementation here
    pass
```

### JavaScript/HTML

- Use consistent indentation (2 or 4 spaces)
- Use meaningful variable names
- Comment complex logic
- Follow existing code patterns

### General

- **Keep it simple**: Write code that's easy to understand
- **DRY principle**: Don't Repeat Yourself
- **Single Responsibility**: Each function/class should do one thing
- **Error handling**: Always handle errors appropriately

---

## Project Structure

Understanding the project structure helps you contribute effectively:

```
AI-Test-Studio/
├── backend/              # Backend API server
│   ├── api/             # API routes (admin, customer, auth, agents)
│   ├── services/        # Business logic (RAG, sync, settings, etc.)
│   ├── rag/             # RAG classes (base, multi-format, ChromaDB, caching)
│   ├── connectors/      # External integrations (TestRail, Confluence)
│   ├── extractors/      # Requirement extraction logic
│   └── app.py          # Main Flask application
├── frontend/             # Frontend interfaces
│   ├── admin/           # Admin UI
│   └── customer/        # Customer UI
├── config/              # Configuration files (env.example)
├── docs/                # Documentation
├── scripts/             # Deployment scripts
├── tests/               # Automated tests
└── storage/             # Data storage (gitignored at runtime)
```

### Where to Make Changes

- **New API endpoints**: `backend/api/`
- **RAG functionality**: `backend/rag/`
- **Frontend UI**: `frontend/`
- **Configuration**: `config/env.example`
- **Documentation**: `docs/`

---

## Testing

### Manual Testing

Before submitting a PR, test your changes:

1. **Test the feature you added/modified**
2. **Test related features** to ensure no regressions
3. **Test on different platforms** if possible (Windows, macOS, Linux)
4. **Test with different document types** (PDF, CSV, Excel, Text)

### Testing Checklist

- [ ] Feature works as expected
- [ ] No console errors
- [ ] No breaking changes to existing functionality
- [ ] Error handling works correctly
- [ ] Documentation updated (if needed)

---

## Pull Request Process

### PR Requirements

1. **Clear description** of what the PR does
2. **Reference related issues** (e.g., "Fixes #123")
3. **Screenshots** (if UI changes)
4. **Testing notes** (what you tested, how to test)
5. **Breaking changes** (if any, clearly documented)

### PR Review Process

1. **Automated checks** (if configured) must pass
2. **Code review** by maintainers
3. **Address feedback** and update PR
4. **Approval** from maintainers
5. **Merge** by maintainers

### PR Template

When opening a PR, include:

```markdown
## Description
Brief description of changes

## Type of Change
- [ ] Bug fix
- [ ] New feature
- [ ] Documentation update
- [ ] Refactoring
- [ ] Other (please describe)

## Testing
How was this tested?

## Checklist
- [ ] Code follows style guidelines
- [ ] Self-review completed
- [ ] Comments added for complex code
- [ ] Documentation updated
- [ ] No new warnings generated
- [ ] Tests pass (if applicable)
```

---

## Reporting Issues

### Before Reporting

1. **Search existing issues** to avoid duplicates
2. **Check documentation** to ensure it's not a configuration issue
3. **Try to reproduce** the issue consistently

### Issue Template

When reporting an issue, include:

```markdown
**Description**
Clear description of the issue

**Steps to Reproduce**
1. Step one
2. Step two
3. ...

**Expected Behavior**
What should happen

**Actual Behavior**
What actually happens

**Environment**
- OS: [e.g., Windows 10, macOS 13, Ubuntu 22.04]
- Python version: [e.g., 3.9.7]
- Ollama version: [e.g., 0.1.15]
- Browser (if UI issue): [e.g., Chrome 120]

**Screenshots/Logs**
If applicable, add screenshots or error logs

**Additional Context**
Any other relevant information
```

---

## Areas for Contribution

We welcome contributions in these areas:

### High Priority

- **Bug fixes**: Fix reported issues
- **Documentation**: Improve clarity and completeness
- **Performance**: Optimize slow operations
- **Testing**: Add test coverage

### Feature Ideas

- **New document formats**: Support additional file types
- **UI improvements**: Enhance user experience
- **API enhancements**: Add new endpoints or features
- **Integration**: Connect with other tools/services

### Documentation

- **Code comments**: Add helpful comments
- **API documentation**: Improve API docs
- **Tutorials**: Create how-to guides
- **Examples**: Add usage examples

---

## Getting Help

If you need help:

1. **Check the documentation**: [README.md](../README.md) and [docs/](.)
2. **Search existing issues**: Look for similar problems
3. **Ask questions**: Open a discussion or issue
4. **Join the community**: (if applicable)

---

## License

By contributing, you agree that your contributions will be licensed under the same license as the project.

---

## Recognition

Contributors will be recognized in:
- Project README (if applicable)
- Release notes
- Project documentation

Thank you for contributing to AI Test Studio! 🎉

