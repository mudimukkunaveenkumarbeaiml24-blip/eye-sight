# 👁️ NeuroVision – AI-Powered Eye Health Monitoring System

## 📖 Overview

NeuroVision is an intelligent eye health monitoring and screen wellness platform designed to help users maintain healthy vision habits in the digital age. The system combines Artificial Intelligence, Eye Image Analysis, Screen Usage Monitoring, and Personalized Recommendations to provide a smarter approach to eye care.

The platform enables users to upload eye images, monitor screen exposure, receive AI-generated insights, and manage screen-related settings to reduce eye strain and improve overall visual health.

---

## 🎯 Objectives

- Monitor eye health using AI-powered analysis.
- Reduce eye strain caused by prolonged screen usage.
- Provide personalized eye-care recommendations.
- Help users maintain healthy screen habits.
- Offer intelligent assistance through conversational AI.

---

## ✨ Features

### 👁️ Eye Image Analysis
- Upload eye images for analysis.
- AI-powered image understanding.
- Visual condition monitoring.
- Eye strain assessment.

### 💡 Smart Screen Wellness
- Brightness recommendations.
- Blue light reduction suggestions.
- Dark mode support.
- Screen usage monitoring.

### 📊 Eye Health Dashboard
- Real-time health insights.
- Screen time tracking.
- Eye strain statistics.
- User activity monitoring.

### 🤖 AI Assistant
- Eye-health related Q&A.
- Personalized recommendations.
- Intelligent guidance for eye care.
- Interactive chatbot experience.

### 💾 Data Management
- User profile management.
- Eye analysis history.
- Recommendation storage.
- Secure data handling.

### 📁 Report Generation
- Eye health summaries.
- Historical analysis reports.
- Personalized improvement suggestions.

---

## 🏗️ System Architecture

```text
User
  │
  ▼
Frontend (HTML, CSS, JavaScript)
  │
  ▼
Flask Backend (Python)
  │
  ├── Gemini API
  │
  ├── MongoDB Atlas
  │
  └── SQLite Database
```

---

## 🛠️ Technologies Used

### Frontend
- HTML5
- CSS3
- JavaScript

### Backend
- Python
- Flask

### Database
- SQLite
- MongoDB Atlas

### Artificial Intelligence
- Google Gemini API
- AI-Based Image Analysis

### Advanced Integration
- MongoDB MCP (Model Context Protocol)

---

## 📂 Project Structure

```text
NeuroVision/
│
├── backend/
│   ├── app.py
│   ├── requirements.txt
│   └── neurovision.db
│
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── script.js
│
├── uploads/
│
├── vercel.json
│
└── README.md
```

---

## ⚙️ Installation Guide

### Step 1: Clone Repository

```bash
git clone https://github.com/yourusername/neurovision.git
cd neurovision
```

### Step 2: Create Virtual Environment

```bash
python -m venv venv
```

### Step 3: Activate Environment

#### Windows

```bash
venv\Scripts\activate
```

#### Linux / Mac

```bash
source venv/bin/activate
```

### Step 4: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 5: Configure Environment Variables

Create a `.env` file:

```env
GEMINI_API_KEY=your_api_key
MONGODB_URI=your_mongodb_connection_string
```

### Step 6: Run Application

```bash
python app.py
```

Application URL:

```text
http://127.0.0.1:5000
```

---

## 🚀 Deployment

### Vercel Deployment

Create a `vercel.json` file:

```json
{
  "version": 2,
  "builds": [
    {
      "src": "app.py",
      "use": "@vercel/python"
    }
  ],
  "routes": [
    {
      "src": "/(.*)",
      "dest": "app.py"
    }
  ]
}
```

### Deploy Steps

1. Push code to GitHub.
2. Login to Vercel.
3. Import GitHub Repository.
4. Deploy Project.
5. Configure Environment Variables.

---

## 🔗 MongoDB MCP Integration

NeuroVision supports future integration with MongoDB MCP (Model Context Protocol).

Benefits include:

- AI-assisted database interaction.
- Personalized recommendation retrieval.
- Intelligent eye-health history analysis.
- Context-aware AI assistance.
- Automated report generation.

---

## 📈 Future Enhancements

- Real-time Eye Tracking
- Blink Detection System
- Vision Quality Assessment
- Mobile Application
- Cloud-Based Analytics
- Multi-User Support
- Voice Assistant Integration
- Wearable Device Connectivity
- Advanced AI Diagnostics
- Smart Notification System

---

## 🔒 Security Features

- Secure API Communication
- Protected User Data
- Environment Variable Management
- Database Access Control
- Safe File Upload Handling

---

## 🎓 Educational Value

This project demonstrates:

- Full Stack Development
- Artificial Intelligence Integration
- Computer Vision Concepts
- Database Management
- API Development
- Cloud Deployment
- Modern Web Technologies

---

## 👨‍💻 Developer

### Naveen Kumar

AI Developer | Full Stack Developer | Computer Science Student

**Project Name:** NeuroVision – AI-Powered Eye Health Monitoring System

---

## 📜 License

This project is developed for educational, research, and innovation purposes.

© 2026 Naveen Kumar. All Rights Reserved.
