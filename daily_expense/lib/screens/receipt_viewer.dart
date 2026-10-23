import 'dart:io';

import 'package:flutter/material.dart';

/// Full-screen, zoomable view of a receipt photo.
class ReceiptViewer extends StatelessWidget {
  const ReceiptViewer({super.key, required this.path});

  final String path;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      backgroundColor: Colors.black,
      appBar: AppBar(
        backgroundColor: Colors.black,
        foregroundColor: Colors.white,
        title: const Text('Receipt'),
      ),
      body: InteractiveViewer(
        maxScale: 6,
        child: Center(child: Image.file(File(path))),
      ),
    );
  }
}
