// Thin client: the language and the debugger live in the Python package `sipl`.
const vscode = require('vscode');

const configurationProvider = {
  async resolveDebugConfiguration(folder, config) {
    if (!config.program) {
      // F5 without launch.json
      const editor = vscode.window.activeTextEditor;
      if (!editor || editor.document.languageId !== 'sipl') {
        vscode.window.showErrorMessage('Відкрийте файл .sipl, щоб запустити налагодження.');
        return undefined;
      }
      config = { type: 'sipl', request: 'launch', name: 'Debug SIPL', program: editor.document.fileName };
    }
    if (config.input === undefined) {
      config.input = await vscode.window.showInputBox({
        title: 'Початковий стан SIPL',
        prompt: 'Значення вхідних змінних',
        placeHolder: 'X=2 Y=10',
      });
      if (config.input === undefined) {
        return undefined; // the user cancelled the prompt
      }
    }
    return config;
  },
};

const adapterFactory = {
  createDebugAdapterDescriptor(session) {
    const python = vscode.workspace
      .getConfiguration('sipl', session.workspaceFolder)
      .get('pythonPath');
    return new vscode.DebugAdapterExecutable(python, ['-m', 'sipl.dap']);
  },
};

function activate(context) {
  context.subscriptions.push(
    vscode.debug.registerDebugConfigurationProvider('sipl', configurationProvider),
    vscode.debug.registerDebugAdapterDescriptorFactory('sipl', adapterFactory),
  );
}

module.exports = { activate };
