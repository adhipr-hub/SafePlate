using System;
using System.Diagnostics;
using System.IO;
using System.Net.Sockets;
using System.Threading;
using System.Windows.Forms;

internal static class SafePlateLauncher
{
    private const string Host = "127.0.0.1";
    private const int Port = 8765;
    private const string Url = "http://127.0.0.1:8765";

    [STAThread]
    private static int Main(string[] args)
    {
        string root = @"C:\Users\adhip\Documents\SafePlate";
        if (!Directory.Exists(root))
        {
            MessageBox.Show(
                "SafePlate folder was not found:\n" + root,
                "SafePlate Launcher",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return 1;
        }

        string python = FindPython(root);
        if (python == null)
        {
            MessageBox.Show(
                "Could not find a Python install that can load SafePlate dependencies.\n\n" +
                "Try running this from PowerShell in the SafePlate folder:\n" +
                "python -m pip install -r requirements.txt",
                "SafePlate Launcher",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error
            );
            return 1;
        }

        if (!IsServerRunning())
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = python,
                Arguments = "scripts\\start_safeplate_app.py --host 127.0.0.1 --port 8765 --no-browser",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                WindowStyle = ProcessWindowStyle.Hidden,
            };

            try
            {
                Process.Start(startInfo);
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "Could not start SafePlate with Python:\n" + ex.Message,
                    "SafePlate Launcher",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return 1;
            }

            if (!WaitForServer())
            {
                MessageBox.Show(
                    "SafePlate started, but the local server did not respond on " + Url + ".",
                    "SafePlate Launcher",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Warning
                );
            }
        }

        OpenUrl(Url);
        return 0;
    }

    private static string FindPython(string root)
    {
        string home = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        string projectVenv = Path.Combine(root, @".venv\Scripts\python.exe");
        string bundled = Path.Combine(
            home,
            @".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
        );
        string envPython = Environment.GetEnvironmentVariable("SAFEPLATE_PYTHON");
        string[] candidates = new string[]
        {
            envPython,
            projectVenv,
            "python",
            bundled,
        };

        foreach (string candidate in candidates)
        {
            if (String.IsNullOrWhiteSpace(candidate))
            {
                continue;
            }
            if (candidate.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) && !File.Exists(candidate))
            {
                continue;
            }
            if (CanLoadSafePlate(candidate, root))
            {
                return candidate;
            }
        }

        return null;
    }

    private static bool CanLoadSafePlate(string python, string root)
    {
        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = python,
                Arguments = "-c \"import safeplate.local_app\"",
                WorkingDirectory = root,
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            using (var process = Process.Start(startInfo))
            {
                if (process == null)
                {
                    return false;
                }
                if (!process.WaitForExit(10000))
                {
                    try
                    {
                        process.Kill();
                    }
                    catch
                    {
                    }
                    return false;
                }
                return process.ExitCode == 0;
            }
        }
        catch
        {
            return false;
        }
    }

    private static bool WaitForServer()
    {
        for (int attempt = 0; attempt < 40; attempt++)
        {
            if (IsServerRunning())
            {
                return true;
            }
            Thread.Sleep(250);
        }
        return false;
    }

    private static bool IsServerRunning()
    {
        try
        {
            using (var client = new TcpClient())
            {
                IAsyncResult result = client.BeginConnect(Host, Port, null, null);
                bool connected = result.AsyncWaitHandle.WaitOne(TimeSpan.FromMilliseconds(250));
                if (!connected)
                {
                    return false;
                }
                client.EndConnect(result);
                return true;
            }
        }
        catch
        {
            return false;
        }
    }

    private static void OpenUrl(string url)
    {
        try
        {
            Process.Start(new ProcessStartInfo
            {
                FileName = url,
                UseShellExecute = true,
            });
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                "SafePlate is running at " + url + ", but the browser did not open:\n" + ex.Message,
                "SafePlate Launcher",
                MessageBoxButtons.OK,
                MessageBoxIcon.Information
            );
        }
    }
}
