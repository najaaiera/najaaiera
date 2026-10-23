# Daily Expense

A simple expense tracker for **Android** and **Windows**, built with Flutter.

## Features

- **Daily expense calculator** – add expenses with an amount, title and category. The app shows the total for the selected day and for the month.
- **Snap receipts** – take a photo of a receipt with the camera on Android, or pick an image from the gallery or a file on Windows. Tap the thumbnail to zoom in.
- **Calendar** – a month, 2-week or week view. Days with expenses show dots, and days with a note show a note icon. Tap a day to see what you spent.
- **Notes** – add a note to each expense, plus a separate note for any day.
- **Monthly summary** – the month's total, daily average and spending by category.
- **Currency** – RM by default. Change it with the ⚙️ button.

Everything is stored offline on the device (`Documents/DailyExpense/data.json`, with receipt photos in `Documents/DailyExpense/receipts/`).

## Download the built app

Each push builds the app with GitHub Actions (**Actions → Build Daily Expense app**):

- `daily-expense-android-apk` → copy `app-release.apk` to your phone and install it.
- `daily-expense-windows` → unzip it and run `daily_expense.exe`. Keep the other files in the same folder.

## Build it yourself

Install [Flutter](https://docs.flutter.dev/get-started/install), then:

```bash
cd daily_expense
flutter pub get
flutter run                    # run on a connected phone or this PC
flutter build apk --release    # Android
flutter build windows --release  # Windows (must run on a Windows PC with Visual Studio "Desktop development with C++")
```
