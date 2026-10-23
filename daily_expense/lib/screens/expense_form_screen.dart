import 'dart:io';

import 'package:flutter/material.dart';
import 'package:image_picker/image_picker.dart';
import 'package:intl/intl.dart';
import 'package:uuid/uuid.dart';

import '../models.dart';
import '../store.dart';
import 'receipt_viewer.dart';

/// Add a new expense, or edit/delete an existing one.
class ExpenseFormScreen extends StatefulWidget {
  const ExpenseFormScreen({
    super.key,
    required this.store,
    this.expense,
    required this.initialDate,
  });

  final ExpenseStore store;
  final Expense? expense;
  final DateTime initialDate;

  @override
  State<ExpenseFormScreen> createState() => _ExpenseFormScreenState();
}

class _ExpenseFormScreenState extends State<ExpenseFormScreen> {
  final _formKey = GlobalKey<FormState>();
  late final TextEditingController _title;
  late final TextEditingController _amount;
  late final TextEditingController _note;
  late String _category;
  late DateTime _date;

  /// Receipt already saved in the app folder.
  String? _savedReceipt;

  /// Newly snapped/picked image, copied into the app folder on save.
  String? _newReceipt;
  bool _saving = false;

  // The camera is only available on phones; on Windows we pick a file.
  final bool _hasCamera = Platform.isAndroid || Platform.isIOS;

  @override
  void initState() {
    super.initState();
    final e = widget.expense;
    _title = TextEditingController(text: e?.title ?? '');
    _amount = TextEditingController(
        text: e == null ? '' : e.amount.toStringAsFixed(2));
    _note = TextEditingController(text: e?.note ?? '');
    _category = e?.category ?? kCategories.first;
    if (e != null) {
      _date = e.date;
    } else {
      // New expense: use the day selected in the calendar with the current time.
      final now = DateTime.now();
      final d = widget.initialDate;
      _date = DateTime(d.year, d.month, d.day, now.hour, now.minute);
    }
    _savedReceipt = e?.receiptPath;
  }

  @override
  void dispose() {
    _title.dispose();
    _amount.dispose();
    _note.dispose();
    super.dispose();
  }

  String? get _currentReceipt => _newReceipt ?? _savedReceipt;

  Future<void> _pickReceipt(ImageSource source) async {
    try {
      final file = await ImagePicker().pickImage(
        source: source,
        maxWidth: 2000,
        imageQuality: 85,
      );
      if (file != null) setState(() => _newReceipt = file.path);
    } catch (err) {
      if (!mounted) return;
      ScaffoldMessenger.of(context)
          .showSnackBar(SnackBar(content: Text('Could not get image: $err')));
    }
  }

  Future<void> _pickDate() async {
    final d = await showDatePicker(
      context: context,
      initialDate: _date,
      firstDate: DateTime(2000),
      lastDate: DateTime(2100, 12, 31),
    );
    if (d != null) {
      setState(() =>
          _date = DateTime(d.year, d.month, d.day, _date.hour, _date.minute));
    }
  }

  Future<void> _pickTime() async {
    final t = await showTimePicker(
        context: context, initialTime: TimeOfDay.fromDateTime(_date));
    if (t != null) {
      setState(() =>
          _date = DateTime(_date.year, _date.month, _date.day, t.hour, t.minute));
    }
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() => _saving = true);
    final id = widget.expense?.id ?? const Uuid().v4();
    var receipt = _savedReceipt;
    if (_newReceipt != null) {
      receipt = await widget.store.importReceipt(_newReceipt!, id);
    }
    await widget.store.upsert(Expense(
      id: id,
      title: _title.text.trim(),
      amount: double.parse(_amount.text.replaceAll(',', '.')),
      category: _category,
      date: _date,
      note: _note.text.trim(),
      receiptPath: receipt,
    ));
    if (mounted) Navigator.of(context).pop();
  }

  Future<void> _delete() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Delete this expense?'),
        content: const Text('Its receipt photo will also be deleted.'),
        actions: [
          TextButton(
              onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel')),
          FilledButton(
              onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Delete')),
        ],
      ),
    );
    if (ok == true) {
      await widget.store.delete(widget.expense!.id);
      if (mounted) Navigator.of(context).pop();
    }
  }

  @override
  Widget build(BuildContext context) {
    final isEdit = widget.expense != null;
    return Scaffold(
      appBar: AppBar(
        title: Text(isEdit ? 'Edit expense' : 'Add expense'),
        actions: [
          if (isEdit)
            IconButton(
                tooltip: 'Delete',
                icon: const Icon(Icons.delete_outline),
                onPressed: _delete),
        ],
      ),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 640),
          child: Form(
            key: _formKey,
            child: ListView(
              padding: const EdgeInsets.all(16),
              children: [
                TextFormField(
                  controller: _amount,
                  autofocus: !isEdit,
                  keyboardType:
                      const TextInputType.numberWithOptions(decimal: true),
                  style: Theme.of(context).textTheme.headlineSmall,
                  decoration: InputDecoration(
                    labelText: 'Amount',
                    prefixText: '${widget.store.currency} ',
                    border: const OutlineInputBorder(),
                  ),
                  validator: (v) {
                    final n = double.tryParse((v ?? '').replaceAll(',', '.'));
                    if (n == null || n <= 0) return 'Enter an amount above 0';
                    return null;
                  },
                ),
                const SizedBox(height: 16),
                TextFormField(
                  controller: _title,
                  textCapitalization: TextCapitalization.sentences,
                  decoration: const InputDecoration(
                    labelText: 'What was it for? (e.g. Lunch)',
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 16),
                DropdownButtonFormField<String>(
                  initialValue: _category,
                  decoration: const InputDecoration(
                    labelText: 'Category',
                    border: OutlineInputBorder(),
                  ),
                  items: [
                    for (final c in kCategories)
                      DropdownMenuItem(value: c, child: Text(c)),
                  ],
                  onChanged: (v) => setState(() => _category = v!),
                ),
                const SizedBox(height: 16),
                Row(
                  children: [
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _pickDate,
                        icon: const Icon(Icons.calendar_month),
                        label: Text(DateFormat('d MMM yyyy').format(_date)),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: OutlinedButton.icon(
                        onPressed: _pickTime,
                        icon: const Icon(Icons.schedule),
                        label: Text(DateFormat('h:mm a').format(_date)),
                      ),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                TextFormField(
                  controller: _note,
                  maxLines: 4,
                  minLines: 2,
                  textCapitalization: TextCapitalization.sentences,
                  decoration: const InputDecoration(
                    labelText: 'Note',
                    alignLabelWithHint: true,
                    border: OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 24),
                Text('Receipt', style: Theme.of(context).textTheme.titleMedium),
                const SizedBox(height: 8),
                _receiptSection(),
                const SizedBox(height: 24),
                FilledButton.icon(
                  onPressed: _saving ? null : _save,
                  icon: _saving
                      ? const SizedBox(
                          width: 18,
                          height: 18,
                          child: CircularProgressIndicator(strokeWidth: 2))
                      : const Icon(Icons.check),
                  label: const Text('Save'),
                  style: FilledButton.styleFrom(
                      minimumSize: const Size.fromHeight(52)),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  Widget _receiptSection() {
    final path = _currentReceipt;
    final buttons = Wrap(
      spacing: 12,
      runSpacing: 8,
      children: [
        if (_hasCamera)
          FilledButton.tonalIcon(
            onPressed: () => _pickReceipt(ImageSource.camera),
            icon: const Icon(Icons.photo_camera),
            label: const Text('Snap receipt'),
          ),
        FilledButton.tonalIcon(
          onPressed: () => _pickReceipt(ImageSource.gallery),
          icon: const Icon(Icons.photo_library),
          label: Text(_hasCamera ? 'From gallery' : 'Choose image'),
        ),
        if (path != null)
          TextButton.icon(
            onPressed: () => setState(() {
              _newReceipt = null;
              _savedReceipt = null;
            }),
            icon: const Icon(Icons.close),
            label: const Text('Remove'),
          ),
      ],
    );

    if (path == null || !File(path).existsSync()) return buttons;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        GestureDetector(
          onTap: () => Navigator.of(context).push(
              MaterialPageRoute(builder: (_) => ReceiptViewer(path: path))),
          child: ClipRRect(
            borderRadius: BorderRadius.circular(12),
            child: Image.file(File(path),
                height: 220, width: double.infinity, fit: BoxFit.cover),
          ),
        ),
        const SizedBox(height: 8),
        buttons,
      ],
    );
  }
}
