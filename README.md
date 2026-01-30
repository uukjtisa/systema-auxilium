# Systema Auxilium

**Current License:** Systema Auxilium Controlled Access License v1.0  
**Author / Creator:** Niccc2007  

## What is Systema Auxilium?

Systema Auxilium (Latin for "System Helper") is an AI-powered desktop assistant that can interact with your operating system through Python code execution. It provides a natural language interface to automate system tasks, manage files, control applications, and perform various operations that would typically require manual scripting or command-line interaction.

**Key Features:**
- Natural language command interface powered by multiple AI providers (Anthropic Claude, Google Gemini, OpenAI via Puter.js)
- Voice mode with speech recognition and text-to-speech capabilities
- System-level Python code execution for task automation
- Desktop GUI built with PyQt6
- Multi-provider AI support with configurable models
- Tool calling system for structured interactions

## About AI System Control

### Is this "bad practice"?

**Short answer: No, when implemented responsibly.**

Systema Auxilium represents a new paradigm in human-computer interaction, where AI serves as an intelligent intermediary between natural language and system operations. While some may view AI-controlled system access as risky, the same concerns apply to any automation tool, scripting language, or remote administration software.

**Consider:**
- **Scripting languages** (Python, PowerShell, Bash) have always allowed programmatic system control
- **Task automation tools** execute system commands based on triggers
- **Remote desktop software** grants external control over systems
- **Package managers** run installation scripts with elevated privileges

The key difference is that Systema Auxilium makes automation accessible through natural language, but the underlying mechanism—executing vetted code—remains the same as traditional scripting.

### Planned Safety Features

Recognizing that not all users are comfortable with immediate code execution, **Systema Auxilium is actively developing enhanced safety mechanisms**:

#### 🔍 Guided Execution Mode (In Development)
A safe mode that displays the AI-generated command **before execution**, allowing users to:
- Review exactly what code will run
- Approve or reject the operation
- Build trust in the system's behavior
- Learn Python through observation

#### 📖 "What Does This Do?" Button (Planned)
For users who aren't familiar with Python or system administration, this feature will:
- Provide plain-English explanations of generated code (AI summarized)
- Break down complex commands into understandable steps
- Highlight potential system impacts
- Serve as an educational tool for non-technical users

### User Responsibility

Like any powerful tool, Systema Auxilium requires responsible use:
- Users should understand the commands they're authorizing
- The system warns about elevated permissions on startup
- It's recommended to run with minimal necessary privileges
- Regular backups are advised (as with any system automation)

**Remember:** Whether you write a Python script manually, copy code from StackOverflow, or use an AI assistant, you are ultimately responsible for code execution on your system. Systema Auxilium simply changes the interface, not the responsibility.

## Recommended Python Environment

It is **strongly recommended** to run this project using **Python 3.10.11**, the exact version used during development.

Using a different Python version may lead to **module incompatibilities or unexpected behavior**, as some dependencies and system-level interactions are sensitive to interpreter changes.

To ensure stability and full compatibility, please verify your Python environment before running or extending this project.

**Official Python 3.10.11 release:**
- https://www.python.org/downloads/release/python-31011/

## Installation & Setup

1. Ensure Python 3.10.11 is installed
2. Install required dependencies: `pip install -r requirements.txt`
3. Configure your AI provider API keys in the settings
4. Run the application: `python main.py`

## Safety Warnings

⚠️ **IMPORTANT SAFETY INFORMATION** ⚠️

- This AI can execute system-level actions if run with sufficient permissions
- Use caution when issuing prompts and commands
- Review generated code before execution (especially once Guided Mode is available)
- You are responsible for all actions taken by the system
- Consider running in a virtual machine or test environment initially
- Keep regular backups of important data

## NOTICE

For legal information, see the `NOTICE` file included in this repository.

---

**Systema Auxilium** - Bridging natural language and system automation through responsible AI integration.
