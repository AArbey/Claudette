1. Instead of having to copy the command to update the runner, make it a simple "update runner" button that runs the update command by itself, and add a "Fix install" button to actually copy the command if the automatic update didn't work. Make sure the automatic update command runs even though it is started by the llm itself
2. Allow user to send messages even while AI is generating, currenlty only way is when ai is asking permission to run command.
3. Maybe tell the ai to use memories when it needs to search for information ?
4. Right now it struggles a lot to find files or understand how something works in the system, can we find a way to make it work better ? maybe a better prompt of such ?
5. Clean up code, remove useless definitions of tests that are just legacy support
6. Clean up the chat interface, right now there a lot of things at once and while the llm generates sometimes 
7. Change name of the app to "Claudette" and generate a logo for the app that will be shown as the favicon too
8. Allow the llm to actually send commands to any runner if it wants
9. The command prefixes are very bad, they are basically useless because often they're just the full command