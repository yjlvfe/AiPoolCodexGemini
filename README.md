# AiPoolCodexGemini 🚀

بوابة وحافظة ذكية لإدارة حسابات الذكاء الاصطناعي المتعددة (ChatGPT/Codex و Google Gemini/Antigravity) محلياً، تدعم التبديل الآلي الفوري بين الحسابات عند انتهاء الكوتا دون فقدان السياق أو تغيير النموذج، مع واجهة تحكم موحدة وسجلات متكاملة.

---

## ✨ المميزات الرئيسية

- **🔄 تدوير الحسابات الفوري (Instant Failover):** عند نفاد رصيد أو كوتا أي حساب (429 / Quota Exceeded)، يتم الانتقال فورياً وتلقائياً للحساب التالي لنفس النموذج بدقة دون تغيير الموديل المطلوب.
- **⚡ تطابق الموديلات 1:1:** توافق كامل مع النماذج الرسمية لـ ChatGPT (`gpt-6-astra`, `gpt-5.6-luna`, `gpt-5.6-sol`, `gpt-5.5`...) ونماذج Gemini الرسمية النظيفة (`gemini-3.8-flash`, `gemini-3.1-pro`...) مع نافذة سياق ضخمة تصل إلى **1,000,000 توكن**.
- **📊 لوحة تحكم وسجلات موحدة (Dashboard & Unified Logs):** واجهة ويب تفاعلية وسلسة تعرض حالة الحسابات ومجمع استهلاك التوكنات، وسجل عمليات زمني مشترك وموحد لكافة الطلبات.
- **🔒 حماية وأمان متقدم:** تسجيل دخول للوحة التحكم بروابط ماجيك مشفرة (Magic Link) مؤقتة عبر بوت تيليجرام مخصص ومحمي.
- **🤖 ربط مباشر وسهل مع الوكلاء:** دعم التكامل المباشر والسريع مع **Hermes Agent** و **OpenClaw** كمزودات أساسية.

---

## 🛠️ متطلبات التشغيل

- **نظام التشغيل:** Linux (Ubuntu/Debian) أو Windows عبر WSL2 (مع تفعيل systemd).
- **البيئة:** Python 3.10+ مع `python3-venv` و `git`.
- **Node.js:** مطلوب فقط في حال استخدام أدوات سطر أوامر Codex الرسمية.

---

## 🚀 خطوات التثبيت والتشغيل الصحيحة

### 1. تثبيت الحزم الأساسية للنظام
```bash
sudo apt update
sudo apt install -y git python3 python3-venv python3-pip
```

### 2. استنساخ المستودع والدخول للمجلد
```bash
git clone https://github.com/yjlvfe/AiPoolCodexGemini.git
cd AiPoolCodexGemini
```

### 3. إعداد ملف البيئة (اختياري)
```bash
cp config.env.example config.env
# قم بتعديل config.env لإضافة إعدادات تيليجرام أو المنافذ إن أردت
```

### 4. تشغيل سكريبت التثبيت الآلي
```bash
bash install.sh
```
*يقوم السكريبت تلقائياً بإنشاء البيئة الافتراضية `.venv` وتثبيت الاعتماديات وإعداد خدمات systemd وإضافتها للتشغيل التلقائي.*

### 5. التحقق من حالة الخدمات
```bash
systemctl --user status ai-codex-bridge ai-gemini-bridge ai-dashboard
```

---

## 🌐 المنافذ ونقاط النهاية الافتراضية (Default Endpoints)

| الخدمة | العنوان والمنفذ | الوظيفة |
| :--- | :--- | :--- |
| **لوحة التحكم (Dashboard)** | `http://127.0.0.1:8444` | إدارة الحسابات، الإحصائيات، والسجلات |
| **بوابة Gemini** | `http://127.0.0.1:8123/v1` | متوافقة مع OpenAI API لـ Gemini Flash / Pro |
| **بوابة ChatGPT / Codex** | `http://127.0.0.1:8124/v1` | متوافقة مع Chat Completions لـ Codex Models |

---

## 🔑 إدارة وإضافة الحسابات (Account Management)

### إضافة حساب جديد:
```bash
# إضافة حساب ChatGPT / Codex
c add

# إضافة حساب Google Gemini / Antigravity
ag add
```

### أوامر الاستخدام والتبديل السريع:
```bash
# عرض قائمة الحسابات وحالتها
clist
aglist

# فحص كوتا واستهلاك الحسابات
cusage
agusage

# التبديل اليدوي السريع بين الحسابات
c1       # التبديل للحساب رقم 1 في كودكس
c2       # التبديل للحساب رقم 2 في كودكس
ag1      # التبديل للحساب رقم 1 في جيميني
ag2      # التبديل للحساب رقم 2 في جيميني
```

---

## 🤖 ربط الوكلاء (Agents Integration)

يمكنك ربط الوكلاء بسهولة عبر أوامر التجهيز الآلية:

```bash
# ربط وتجهيز Hermes Agent بمزودي Gemini و ChatGPT
python3 scripts/setup-hermes.py

# ربط وتجهيز OpenClaw
python3 scripts/setup-openclaw.py
```

---

## 🔄 التحديث (Updating)

لتحديث المشروع بأمان وجلب آخر الميزات والإصلاحات:
```bash
bash update.sh
```
*أو يمكنك التحديث بضغطة زر واحدة مباشرة من تبويب الإعدادات داخل لوحة التحكم.*

---

## 🧪 فحص واختبار النظام (Verification & Tests)

للتأكد من سلامة جميع أجزاء النظام ومطابقة الاختبارات:
```bash
.venv/bin/python -m unittest discover -s tests -v
```

---

## 📄 الترخيص وحقوق الملكية (License)

هذا المشروع مرخص تحت رخصة **MIT License**.

جميع الحقوق محفوظة ومملوكة للمبرمج:
**يوسف القحطاني — Youssef Al-Qahtany** ([@YJLVFE](https://github.com/yjlvfe))  
Telegram: [@YJLVFE](https://t.me/YJLVFE)
