#!/bin/bash
# Simple launcher for the Overcode demo

echo "🎭 Simple Overcode Demo"
echo "========================="
echo ""
echo "This will launch two Claude instances:"
echo "  1. times-tables - Generating multiplication tables"
echo "  2. recipes - Creating creative recipes"
echo ""
echo "Starting in 3 seconds..."
sleep 3

python3 simple_overcode.py launch

echo ""
echo "✅ Demo launched!"
echo ""
echo "Ask your current Claude for updates, or run:"
echo "  python simple_overcode.py status"
echo ""
