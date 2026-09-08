# 🚀 منظومة إدارة وتعدد حسابات الذكاء الاصطناعي الموحدة (AI Multi-Account Pool & Gateway Suite)

منظومة موحدة متكاملة لإدارة وتعدد حسابات **Gemini** و **Codex (ChatGPT)**، وتوفير جسور محلية عالية السرعة متوافقة 100% مع واجهة OpenAI القياسية (`/v1/chat/completions`)، مع لوحة تحكم ويب لحظية وبوت تيليجرام لتوليد روابط الدخول السريعة المشفرة.

---

## 🌟 مميزات المنظومة:

1. **جسر Gemini (Antigravity Gateway):**
   - منفذ الاستماع: `8123`
   - يدعم التبديل اللحظي بين عدة حسابات (`ag1`, `ag2`, `ag3`, ...).
   - تجديد تلقائي لتوكنات المصادقة (OAuth Auto-Refresh).
   - متوافق مع كافة نماذج Gemini و Claude و OSS.

2. **جسر Codex (ChatGPT Gateway):**
   - منفذ الاستماع: `8124`
   - يدعم التبديل اللحظي بين حسابات ChatGPT Plus المخفضة والمشتركة (`c1`, `c2`, `c3`, ...).
   - فحص دقيق للحصص والاستهلاك الأسبوعي ونافذة 5 ساعات.
   - متوافق مع كافة نماذج Astra و Luna و Sol.

3. **لوحة التحكم والمراقبة (Dashboard):**
   - منفذ الاستماع: `8444` (أو عبر البروكسي `/aipool/`).
   - سجل عمليات مشترك (Real Activity Stream) لآخر 100 عملية مع توقيت 24 ساعة رقمي (`MM/DD HH:MM`).
   - عدادات توكنات فورية دقيقة بدون إبطاء أو فحص ثقيل للحسابات.
   - قائمة حسابات قابلة للطي (مخفية افتراضياً وتظهر بنقرة زر).
   - حماية كاملة بروابط ماجيك مشفرة صالحة لـ 30 دقيقة عبر بوت التيليجرام.

4. **أدوات سطر أوامر موحدة فائقة السهولة:**
   - أوامر `ag` و `cx` لإدارة الحسابات بنقرة واحدة.
   - الحفاظ على كافة الاختصارات المعتادة (`ag1..N`, `c1..N`, `agusage`, `cusage`).

---

## 📂 هيكل المشروع:

```text
ai-pool-suite/
├── README.md                      # هذا الدليل الشامل
├── install.sh                     # سكريبت التثبيت والتشغيل التلقائي بنقرة واحدة
├── config.env                     # ملف الإعدادات الموحد والمنافذ
├── bridges/
│   ├── gemini_bridge.py           # جسر Gemini على البورت 8123
│   └── codex_bridge.py            # جسر Codex على البورت 8124
├── dashboard/
│   ├── server.py                  # خادم لوحة التحكم على البورت 8444
│   ├── app.py                     # معالج بيانات الحسابات والسجلات
│   └── bot_service.py             # بوت التيليجرام للمصادقة السريعة
├── cli/
│   ├── ag                         # الأداة الموحدة لحسابات Gemini
│   ├── cx                         # الأداة الموحدة لحسابات Codex
│   ├── antigravity-account-switch # محرك تبديل حسابات Gemini
│   ├── codex-account-switch       # محرك تبديل حسابات Codex
│   └── codex-account-query        # محرك استعلام حصص حسابات Codex
├── systemd/                       # ملفات خدمات النظام
└── examples/                      # نماذج الإعدادات الجاهزة لهيرمس وأوبن كلاو
```

---

## ⚡ خطوات التثبيت والتشغيل على أي سيرفر جديد:

### الخطوة 1: نقل المجلد
انسخ مجلد `ai-pool-suite` إلى السيرفر الجديد في المسار:
`/root/Projects/ai-pool-suite`

### الخطوة 2: تشغيل سكريبت التثبيت
من داخل المجلد، نفّذ أمراً واحداً فقط:
```bash
chmod +x install.sh
./install.sh
```
يقوم السكريبت تلقائياً بـ:
- تجهيز المجلدات وإعطاء الصلاحيات.
- تثبيت كافة أوامر الـ CLI في `/usr/local/bin` (`ag`, `cx`, `ag1..N`, `c1..N`, `agusage`, `cusage`).
- تسجيل وتفعيل وتشغيل خدمات Systemd الأربعة تلقائياً.

---

## 🔑 كيفية إضافة وإدارة الحسابات:

### 1. حسابات Gemini (Antigravity):
- **عرض الحسابات:** `ag` أو `ag list`
- **التبديل لحساب:** `ag 2` أو `ag2`
- **فحص الاستهلاك:** `ag usage` أو `agusage`
- **إضافة حساب جديد وتفعيله فوراً:**
  ```bash
  ag add
  ```
  *(يقوم تلقائياً بحجز أول خانة فارغة وبدء إجراء تسجيل الدخول الفوري للحساب)*

### 2. حسابات Codex (ChatGPT):
- **عرض الحسابات:** `cx` أو `cx list`
- **التبديل لحساب:** `c 1` أو `c 2` أو `cx 3`
- **فحص الاستهلاك:** `cx usage` أو `cusage`
- **إضافة حساب جديد وتفعيله فوراً:**
  ```bash
  c add
  # أو
  cx add
  ```
  *(يقوم فوراً بحجز الخانة التالية تلقائياً وبدء جلسة تسجيل الدخول الرسمية برمز الجهاز وضع الكود وتنشيطه)*

---

## 🗑️ إلغاء التثبيت وحذف الخدمات (Uninstall):

إذا أردت في أي وقت إيقاف الخدمات وحذف كافة الأوامر والاختصارات من النظام:
```bash
./uninstall.sh
```
*(يقوم بإيقاف وتعطيل خدمات Systemd الأربعة وإزالة كافة الأوامر من `/usr/local/bin` بنظافة تامة، مع إبقاء ملفات حساباتك محفوظة كأمان).*

---

## 🔗 الربط التلقائي بنقرة واحدة (Hermes & OpenClaw):

بدلاً من نسخ ولصق الإعدادات يدوياً، وفرنا أمرين مستقلين للربط التلقائي الفوري:

### 1. ربط المزودات في هيرمس (Hermes Agent):
```bash
./setup-hermes.sh
```
*(يقوم تلقائياً بتحديث `~/.hermes/config.yaml` وإضافة مزودي `gemini` و `codex` مع كافة الموديلات).*

### 2. ربط المزودات في أوبن كلاو (OpenClaw):
```bash
./setup-openclaw.sh
```
*(يقوم تلقائياً بتحديث `~/.openclaw/openclaw.json` وإضافة مزودي `gemini_pool` و `codex_pool`).*

---

### أو الربط اليدوي إذا رغبت:

#### في هيرمس (`~/.hermes/config.yaml`):

```yaml
custom_providers:
  # مزود Gemini
  gemini:
    base_url: "http://127.0.0.1:8123/v1"
    api_key: "dummy-key"
    models:
      - id: "gemini-3.8-flash-tiered"
        display_name: "Gemini 3.8 Flash"
        context_window: 1048576

  # مزود Codex (ChatGPT)
  codex:
    base_url: "http://127.0.0.1:8124/v1"
    api_key: "dummy-key"
    models:
      - id: "gpt-6-astra"
        display_name: "GPT-6 Astra"
        context_window: 128000
      - id: "gpt-5.6-luna"
        display_name: "GPT-5.6 Luna"
        context_window: 128000
```

---

## 🔗 ربط المنظومة مع OpenClaw:

في إعدادات المزودات في `openclaw.json`:

```json
{
  "gemini_pool": {
    "baseUrl": "http://127.0.0.1:8123/v1",
    "apiKey": "any-key",
    "models": ["gemini-3.8-flash-tiered"]
  },
  "codex_pool": {
    "baseUrl": "http://127.0.0.1:8124/v1",
    "apiKey": "any-key",
    "models": ["gpt-6-astra", "gpt-5.6-luna"]
  }
}
```

---

## 🛠️ إدارة الخدمات (Systemd):

لإعادة تشغيل أي خدمة أو فحص حالتها:
```bash
# جسر Gemini
systemctl --user status ai-gemini-bridge

# جسر Codex
systemctl --user status ai-codex-bridge

# لوحة التحكم
systemctl --user status ai-dashboard

# بوت التيليجرام
systemctl --user status ai-bot
```

---

## 📱 الوصول للوحة التحكم:
أرسل الأمر `/dashboard` أو `/link` إلى بوت التيليجرام الخاص بك، وسيرسل لك رابط الماجيك المشفر للدخول مباشرة بضغطة زر دون الحاجة لكتابة كلمات مرور!
