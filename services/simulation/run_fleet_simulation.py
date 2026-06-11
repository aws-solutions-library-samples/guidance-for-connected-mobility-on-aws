#!/usr/bin/env python3
"""
Fleet Simulation CLI
Simple command-line interface for running fleet simulations
"""

import argparse
import sys
import time
import os

# Use relative imports since we're in the same directory
from fleet_simulation_runner import SimulationRunner, SimulationConfig

def main():
    parser = argparse.ArgumentParser(
        description="Dynamic Fleet Simulation System",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Quick 5-minute test with 3 vehicles
  python run_fleet_simulation.py --quick
  
  # 30-minute simulation with 10 vehicles in Seattle
  python run_fleet_simulation.py --duration 30 --vehicles 10 --city seattle
  
  # High safety event simulation for testing
  python run_fleet_simulation.py --duration 15 --vehicles 5 --safety-rate 0.4 --no-cleanup
  
  # Custom simulation with specific parameters
  python run_fleet_simulation.py --duration 60 --vehicles 20 --city seattle --safety-rate 0.2 --interval 45
        """
    )
    
    # Simulation parameters
    parser.add_argument('--trips', type=int, default=3,
                       help='Number of trips per vehicle (default: 3)')
    parser.add_argument('--vehicles', type=int, default=10,
                       help='Number of vehicles to simulate (default: 10)')
    parser.add_argument('--city', type=str, default='seattle',
                       help='City for route generation (default: seattle)')
    parser.add_argument('--safety-rate', type=float, default=0.15,
                       help='Safety event probability 0.0-1.0 (default: 0.15)')
    parser.add_argument('--interval', type=int, default=30,
                       help='Update interval in seconds (default: 30)')
    parser.add_argument('--fleet-prefix', type=str, default='SIM',
                       help='Fleet ID prefix (default: SIM)')
    
    # Options
    parser.add_argument('--quick', action='store_true',
                       help='Run quick 5-minute test with 3 vehicles')
    parser.add_argument('--no-cleanup', action='store_true',
                       help='Do not clean up simulation data after completion')
    parser.add_argument('--non-interactive', action='store_true',
                       help='Run without user prompts (for API calls)')
    parser.add_argument('--status', action='store_true',
                       help='Show simulation status and exit')
    
    args = parser.parse_args()
    
    # Handle quick test
    if args.quick:
        print("🧪 Running Quick Test Simulation")
        print("=" * 35)
        config = SimulationConfig(
            trips_per_vehicle=1,
            num_vehicles=3,
            fleet_id_prefix="TEST",
            city="seattle",
            safety_event_probability=0.3,
            update_interval_seconds=15,
            reset_data_after=not args.no_cleanup
        )
    else:
        # Validate parameters
        if args.trips <= 0:
            print("❌ Number of trips must be positive")
            sys.exit(1)
        if args.vehicles <= 0:
            print("❌ Number of vehicles must be positive")
            sys.exit(1)
        if not 0.0 <= args.safety_rate <= 1.0:
            print("❌ Safety rate must be between 0.0 and 1.0")
            sys.exit(1)
        if args.interval <= 0:
            print("❌ Update interval must be positive")
            sys.exit(1)
        
        print("🚀 Dynamic Fleet Simulation")
        print("=" * 30)
        config = SimulationConfig(
            trips_per_vehicle=args.trips,
            num_vehicles=args.vehicles,
            fleet_id_prefix=args.fleet_prefix,
            city=args.city,
            safety_event_probability=args.safety_rate,
            update_interval_seconds=args.interval,
            reset_data_after=not args.no_cleanup
        )
    
    # Display configuration
    print(f"Configuration:")
    print(f"  Trips per Vehicle: {config.trips_per_vehicle}")
    print(f"  Vehicles: {config.num_vehicles}")
    print(f"  City: {config.city}")
    print(f"  Fleet Prefix: {config.fleet_id_prefix}")
    print(f"  Safety Event Rate: {config.safety_event_probability * 100:.1f}%")
    print(f"  Update Interval: {config.update_interval_seconds}s")
    print(f"  Cleanup After: {'Yes' if config.reset_data_after else 'No'}")
    
    # Confirm before starting (only if interactive and not quick test)
    import sys
    is_interactive = sys.stdin.isatty() and not args.non_interactive
    
    if not args.quick and is_interactive:
        try:
            confirm = input(f"\nStart simulation? (y/n): ").lower()
            if confirm != 'y':
                print("👋 Cancelled")
                sys.exit(0)
        except (EOFError, KeyboardInterrupt):
            print("\n👋 Cancelled")
            sys.exit(0)
    elif not is_interactive or args.non_interactive:
        print(f"\n🤖 Running in non-interactive mode - starting simulation automatically")
    else:
        print(f"\n🚀 Starting quick test simulation...")
    
    # Create and run simulation
    try:
        print(f"\n🔧 Initializing simulation...")
        runner = SimulationRunner(config)
        runner.initialize_simulation()
        
        print(f"\n🚀 Starting simulation...")
        print(f"Press Ctrl+C to stop early")
        runner.start_simulation()
        
        # Wait for completion
        while runner.simulation_active:
            time.sleep(1)
        
        print(f"\n✅ Simulation completed successfully!")
        print(f"Check your dashboard at: http://localhost:5177")
        print(f"Safety events should be visible in the fleet overview and map.")
        
    except KeyboardInterrupt:
        print(f"\n⏹️  Simulation interrupted by user")
        if 'runner' in locals():
            runner.stop_simulation()
    except Exception as e:
        print(f"\n❌ Simulation failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
