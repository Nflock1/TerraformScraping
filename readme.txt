This is a software is a work in progress that scrapes terraform's web documentation to
gain a comprehensive, context dependant understanding of how a .tf file should be structured.
It then uses the contextual data structure that it creates to translate a K8s YAML line by
line to create a mostly accurate .tf file (sometimes there is guesswork due to custom mappings 
that cannot be completely tested, however it has performed well on my tests thus far).

To use this program, one should put relevant k8s files into the YAML folder and then
run "pip install requests" before running the main.js file with either the "run" button/option
in your IDE or with the following command:
& "C:/path_to_python.exe" c:/path_to_cloned_repo/main2.py. 

The generated tf files will then be placed into a freshly created folder "tf files".



NOTES:

- This software only works with Kubernetes "resource" types.

- multi-resource YAMl files are not supported

- since I began working on this, terraform completely overhauled their documentation to 
improve clarity, which is great for developers and programatic scraping but quite code 
breaking for this version of the software I wrote. Therefore I hardcoded in an older version
of the docs to scrape, but left in the mechanism for finding the latest docs for future use.

- there may be issues with scraping/translating other resource types than those I provided
because terraforms old documentation is not standardized and has many formatting anomolies
spread throughout their pages. Though I have accounted for many of these already, the only
way to identify more is through further testing (which I plan to do). One example I know exists
is for Horizotal Pod Autoscalers, the optional children of the "metric" block depend on 
the required children (the type). I will implement this fix down the line.

Plans for the future:

I would like to finish fleshing out the small bugs of this software and then move on to
working on a second version that leverages Terraform's WONDERFUL new documentation formatting.
Further, I believe that this code may have room for refactoring into smaller and more readable
methods. Much of this code is context dependant and entangled in a way that made it very 
difficult to design re-usable functions, so breaking it down further offered marginal utility
when trying to flesh out the core functionality. ie: there was not a functional benefit to 
compartmentalizing code, but now I have a bit of a monolith...

Once I get some of the last kinkes related to translating formatting anomolies (like for HPAs) 
ironed out, I would also like to execute a full test where I translate some working kubernetes
files into .tf and deploy them onto AWS or some other hosting platform to verify that this 
tool would work the way I intended it to be and to identify any shortcomings in the user experience.

Ladtly, I think it would be cool to include some functionality for linking resource trees together:
there are times where attributes within one resource are specified in a redirect to another resource
doc (like the volume claim spec in the deployment resource). Right now this creates a bit of a gap
in translating the more complex YAMLs that would be nice to close.