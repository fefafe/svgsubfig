%% Plot 1

x = pi .* (0 : 0.01 : 6);

figure();
plot(x, sin(x), Color=fefa_color("red"));
plot(x, cos(x .^ (1/3)), Color=fefa_color("darkblue"));
xlabel('x');
ylabel('y');
legend({'sin', 'cos'});
set(gca, Color="none");
set(gcf, InvertHardcopy="off", Color="None");
print('plot_1.svg', "-dsvg", "-vector");
close();


%% Plot 2

x = pi .* (-2 : 0.01 : 2);

figure();
plot(x, x .^ 2, Color=fefa_color("orange"));
plot(x, x .^ 3, Color=fefa_color("pink"));
xlabel('x');
ylabel('y');
legend({'x^2', 'x^3'}, Location="northwest");
set(gca, Color="none");
set(gcf, Color="None");
print('plot_2.png', "-dpng", "-r300");
close();
