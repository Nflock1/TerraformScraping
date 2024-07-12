import os, shutil, requests, re, json

extra_data_flag = False
cache_resource_trees = False

def print_array(arr):
  for element in arr:
    print(element.strip() if isinstance(element, str) else element)


# Purpose:
#   This function is designed to translate a single key value pair from a line in a k8s yaml into a corresponding
#   .tf format 
# params:
#  KV_pair: this is an array that holds they k8s Key and the value assigned to it in the YAML file. For items with sub
#       attributes, this will hold ["key"]. For maps, it wil be ["key {"] with varied number of spaces
#  attribute_tree: this is a dictionary tree that stores the attribute and sub attribute mappings
#  tree_log: this is a stack holding the attributes which have been accessed recently so that we can quickly index
#       through the attribute_tree
# returns :
#   an array of strings with ['TERRAFORM_NAME', 'formatted Terraform line']

def terra_translate(KV_pair, attribute_tree, tree_log):
  # Sometimes there are multi-level custom fields that require placeholders in tree_log for formatting.
  # These will never have a match within the attribute_tree.
  skip_rating = False
  best_match = ''
  best_rating = 0

  snake_case_k8s_key = KV_pair[0]
  #YAML camelCase into Terraform's snake_case
  if re.search(r"(?<=[a-z])(?=[A-Z])", KV_pair[0]) is not None:
    snake_case_k8s_key = re.sub(r'(?<=[a-z])(?=[A-Z])', '_', KV_pair[0]).lower()

  #find the dictionary entry for the current k8s attribute
  this_attribute = attribute_tree[list(attribute_tree.keys())[0]]
  # use logged path to navigate dictionary tree to the current branch 
  for branch in tree_log:
    #some stack entries are for custom pieces not in the attribute dictionary
    if branch == "custom_field_placeholder":
      skip_rating = True
      break
    if this_attribute['children'] is None:
      break
    this_attribute = this_attribute['children'][branch]
  # evaluate each child for a matching score and find the best.
  if this_attribute['children'] is not None and not skip_rating:
    for child in this_attribute['children']:
      rating = 0
      terra_key = child
      
      if snake_case_k8s_key in terra_key:
        # bonus for a matching
        rating += 20
        # scaled bonus where priority is given based on how much of the word is exact match
        # helps choose between things like "name" and "namespace" 
        rating += 30 - (len(terra_key) - len(snake_case_k8s_key))

      #look for reverse matching to identify attributes that Terraform syntax shortened
      if terra_key in snake_case_k8s_key:
        rating += 20
        rating += 30 - (len(snake_case_k8s_key) - len(terra_key))

      if rating > best_rating:
          best_match = terra_key
          best_rating = rating

  # decide if the entry should be KV entry or a mapping.
  formatted = None
  if best_match == '':
    if re.match(r"[\[\]]+", KV_pair[1]) is not None:
      formatted = formatted = f'{snake_case_k8s_key} = {KV_pair[1]}'
    elif KV_pair[1] != '':
      pre_process_quotes = KV_pair[1].replace('"',"'")
      formatted = f'{snake_case_k8s_key} = "{pre_process_quotes}"'
    elif KV_pair[1] == '':
      formatted = f'{snake_case_k8s_key} = '
      tree_log.append("custom_field_placeholder")
  elif this_attribute['children'][best_match]['children'] is None:
    #best match has no dictionary children
    if re.match(r"[\[\]]+", KV_pair[1]) is not None:
      formatted = formatted = f'{snake_case_k8s_key} = {KV_pair[1]}'
    elif KV_pair[1] == '':
      # this attribute is likely a custom mapping that will not be documented. such as custom labels
      formatted = f'{best_match} = '
      tree_log.append(best_match)
    else:
      #no children and it has a value. IE: it is a KV entry
      pre_process_quotes = KV_pair[1].replace('"',"'")
      formatted = f'{best_match} = "{pre_process_quotes}"'

  else:
    # the best match has children and thus is a mapping
    formatted = f'{best_match}'
    tree_log.append(best_match)
      
  return [best_match, formatted]

# Purpose:
#   This meant to find the relevant provider doc data entry so it can be used to obtain the raw data
#   that is used to render terraform's hashicorp documentation for K8s resources
# params:
#    provider docs: a list of all kubernetes provider resource document info that is used to dynamically load documentation.
#       These JSON objects hold relative links/slugs to documententation for each k8s resource.
#    resource: 
#       The name of the K8s resource that was sourced from the list of provided k8s YAMLS
# returns :
#    k8s_hashicorp_document - one of the provider_docs param that matches the provided resource param

def parse_recent_versions(provider_docs, resource, resource_version):
  # for every terraform document found for kubernetes
  k8s_hashicorp_document = None
  for doc in provider_docs:
    doc_name = doc["attributes"]["slug"]
    if doc_name.replace("kubernetes_","") != f"{resource}_{resource_version}" or doc["attributes"]["category"] != "resources":
      continue
    else:
      if resource_version in doc_name:
        k8s_hashicorp_document = doc
        break
    
  # cleanup by removing version numbers
  k8s_hashicorp_document["attributes"]["slug"] = k8s_hashicorp_document["attributes"]["slug"].split("_v")[0]

  return k8s_hashicorp_document

# Purpose:
#   This function is designed to scrape a resource doc from Terraform's page and format it into a hierarchial Tree 
#   of Terraform's Kubernetes attributes that can be used to translate between K8s and Terraform. 
# params:
#    URL: this is the top level URL that holds references to all provider versions of Terraform's documentation page
#    resource: 
#       The name of the K8s resource that was sourced from the list of provided k8s YAMLS
# returns :
#    A dictionary of k8s attributes that are used in .tf files and have been arranged into a hierarchial tree to aid in
#    contextual interpretation (a pod's 'security_context' is different than a volume_claim_template's 'security_context').

def scrape(URL, resource, resource_version):
  provider_versions = requests.get(URL)
  #NOTE: Terraform finally OVERHAULED their documentation to make nested schemas, but this code will need patching to adjust
  WORKING_PROVIDER_VERSION = 53551
  recent_provider_version = provider_versions.json()["data"]["relationships"]["provider-versions"]["data"][-1]["id"]
  provider_info = requests.get(
    f"https://registry.terraform.io/v2/provider-versions/{WORKING_PROVIDER_VERSION}?include=provider-docs")  # %2Chas-cdktf-docs on the end?
  provider_docs = provider_info.json()["included"]
  k8s_hashicorp_document = parse_recent_versions(provider_docs, resource, resource_version)
  concise_data = [{k8s_hashicorp_document["attributes"]["slug"]: {'children': {}}}]
  # keep reference to actual object in stack so that referencing depth isnt an issue
  json_data = requests.get(f'https://registry.terraform.io{k8s_hashicorp_document["links"]["self"]}').json()
  page_data = json_data["data"]['attributes']
  lines = page_data['content'].split('\n')
  # for when attributes are listed together: 'a' and 'b' have the same children, so there is a section "a/b"
  double_flag = False
  # for identifying when we have found a "the following" block to be skipped
  skip_the_following_lines = False
  #for identifying when we are actively parsing "The following" block's attributes
  skipping_the_following_body = False
  # build array of attribute trees to be strung together
  for index, text in enumerate(lines):
    # sometimes docs include redundant references to attributes that are formatted the same as their copies
    # this creates weird document parsing that generates incorrect dictionary refrences so we ignore one set
    if skip_the_following_lines == False and re.search(r".*[tT]he following.*:", text) is not None:
      # subroutine for identifying if this phrasing is used directly after "argument Reference" header
      run_loop = True
      look_back_depth = 1
      #TODO: may be best running a for loop on this? OR while look_back_depth<=index
      while run_loop:
        #find last non-whitespace line and check it for arg ref header to determine if it shuold be ignored
        if len(lines[index - look_back_depth].strip()) > 0 and re.search(r"[aA]rgument [Rr]eference", lines[index - look_back_depth]) is None: # "Argument Reference" not in lines[index - look_back_depth]:
          skip_the_following_lines = True
          run_loop = False
        elif re.search(r"[aA]rgument [Rr]eference", lines[index - look_back_depth]) is not None: # "Argument Reference" in lines[index - look_back_depth]:
          run_loop = False
        look_back_depth += 1
    elif skip_the_following_lines == True:
      # TODO: '*' should probs be indent_symbol
      if skipping_the_following_body == False:
        if len(text.strip()) == 0:
          continue
        elif re.search(r"^\*|```", text.lstrip()):
          skipping_the_following_body = True
          continue
      else:
        if len(text.lstrip()) == 0 or re.search(r"^\*|```", text.lstrip()):
          skipping_the_following_body = False
          skip_the_following_lines = False

    if skip_the_following_lines:
      continue

    # recognize attribute headers 
    argument = re.search(fr'(###\s)(.*\`.*\`)', text)

    if argument is not None:  # found new attribute with children
      # check if object is explicitly not map to avoid erroneous children assignement and circular dependencies
      # NOTE: this only accounts for 2 multiblocks.
      multi_block = re.search(r'([a-zA-Z_ ]*)([^a-zA-Z_]*)([a-zA-Z _]*)', argument.group(2).replace("`", ""))
      if multi_block is None or len(multi_block.group(3)) == 0:
        double_flag = False
        concise_data.append(
          {argument.group(2).strip('\'\" ').replace("`", ""): {'children': {}, 'has_parent': False, "can_have_children": True}})
      else:
        concise_data.append({multi_block.group(1).strip(): {'children': {}, 'has_parent': False, "can_have_children": True}})
        concise_data.append({multi_block.group(3).strip(): {'children': {}, 'has_parent': False, "can_have_children": True}})
        double_flag = True
    
    # future todo: How could I handle children attributes referenced in other docs? look for links in the description to other 
    # terraform resource docs and explore deeper? ex: volume claim spec in deployments
    
    # recognize children of attribute header
    argument = re.search(fr'(\*\s\`)(.*?)(\`)', text)
    if argument is None:
      continue      

    # todo: may want to search just the description instead of the whole line?

    # NOTE: some number type attributes are not recognized with "[nN]umber of". Most 
    #       are and it's safer to not diqualify these niche cases from having children
    # looks for descriptions holding explicit datatypes that will not result in children'
    attribute_is_mapping = (
      re.search(r"(is\san*)(\s+[A-Za-z]*\s*)([sS]tring|[iI]nt|[Nn]umber|[nN]ame)+", text) is None 
      and re.search(r"[nN]ame of|[nN]umber of", text) is None
    )
    # looks for references to other resource docs so local attributes arent erroneously assigned
    attribute_has_no_redirect = re.search( r"see .* for reference", text) is None
    # recognizes selector querys that will need to be written as maps in .tf file
    attribute_is_query = re.search(r"query", text) is not None
    res_key = list(concise_data[-1].keys())[0]
    concise_data[-1][res_key]['children'][argument.group(2)] = {'children': {} if attribute_is_query else None, 'has_parent': True, "can_have_children": attribute_is_mapping and attribute_has_no_redirect}
    if double_flag:
      # NOTE: again this assumes only 2 doubles
      res_key = list(concise_data[-2].keys())[0]
      concise_data[-2][res_key]['children'][argument.group(2)] = {'children': {} if attribute_is_query else None, 'has_parent': True, "can_have_children": attribute_is_mapping and attribute_has_no_redirect}
  
  # first loop finds the repeated naming conventions and linkes them to the proper parent to avoid circular dependancies
  for data in concise_data:
    data_key = list(data.keys())[0]
    if "deployment" in k8s_hashicorp_document["attributes"]["slug"] and "pod security_context" in data_key:
      #NOWTODO: wth is this - need to map pod to the template?
      continue
    if " " in data_key:
      split_name = data_key.split(" ")
      parent_object = next((x for x in concise_data if list(x.keys())[0] == split_name[0].lower()), None)
      parent_name = split_name[0].lower()
      child_name = split_name[1].lower()


      #NOTE: this will run on every child, but I may improve upon this logic later
      for i in range(0, len(parent_object[parent_name]['children'])):
        if child_name in list(parent_object[parent_name]['children'].keys()) and parent_object[parent_name]['children'][child_name]['can_have_children']:
          parent_object[parent_name]['children'][child_name] = data[data_key]
          data[data_key]['has_parent'] = True
          break
      continue

  # second loop goes through each child entry and links it to nearest matching object
  # NOTE: 'parent is none' check ensures that duplicate name atttributes are assigned to their nearest duplicate.
  for parent_data in concise_data:
    parent_data_key = list(parent_data.keys())[0]
    children_keys = list(parent_data[parent_data_key]['children'].keys())
    for child_key in children_keys:
      #skip over children that have already been found
      if parent_data[parent_data_key]['children'][child_key]['children'] is not None:
        continue
      for this_data in concise_data:
        # shouldn't be re-assigning already assigned objects
        this_data_key = list(this_data.keys())[0]
        if this_data_key == child_key and this_data[this_data_key]['has_parent'] is False and parent_data[parent_data_key]['children'][child_key]['can_have_children']:
          parent_data[parent_data_key]['children'][child_key] = this_data[this_data_key]
          this_data[this_data_key]['has_parent'] = True
          break
  
  # third loop attempts to connect any missing links
  for parent_data in concise_data:
    parent_data_key = list(parent_data.keys())[0]
    children_keys = list(parent_data[parent_data_key]['children'].keys())
    for child_key in children_keys:
      if parent_data[parent_data_key]['children'][child_key]['children'] is not None or parent_data_key == child_key:
        #continue if they already have children or trying to link to self
        continue
      for this_data in concise_data:
        this_data_key = list(this_data.keys())[0]
        if this_data_key == child_key and parent_data[parent_data_key]['children'][child_key]['can_have_children']:
          parent_data[parent_data_key]['children'][child_key] = this_data[this_data_key]
  # first element should be assembled tree
  return concise_data[0]

  # todo: parse which fields are required vs optional to help giv euser feedback on files

# Purpose:
#   This function is designed to pre-process YAML env blocks into a format that is more intuitive for the 
#   interpretation software (each variable in a single k8s YAML env block need's its own env blcok in .tf).
# params:
#    original_file_lines: 
#       An array of the YAML file lines taken from the file that needs to be interpreted
#    initial_index:
#       This is the line number that the env block starts on within the original_file_lines array
# returns :
#    An array of file lines that swaps the original env block with a new set of processed file lines.
def process_env_block(original_file_lines, initial_index):
  processed_env_lines = []
  env_indent_level = len(original_file_lines[initial_index]) - len(original_file_lines[initial_index].lstrip())
  # - indicates block start and end
  # decrement to original indent level indicates end and return
  final_i=None
  for i in range(initial_index + 1, len(original_file_lines)):
    this_indent_level = len(original_file_lines[i]) - len(original_file_lines[i].lstrip())
    this_line = original_file_lines[i].lstrip()
    if re.search(r'#+.*', this_line):
      continue 
    if this_indent_level<=env_indent_level :
      #means that we are done pre processing env
      final_i=i
      break
    if re.search(r'^-.*', this_line):
      last_line = original_file_lines[i-1].strip()
      # new block identified
      #todo: should make sure it starts with a dash via regex
      if "env:" in last_line:
        #todo: imporve with regex
        processed_env_lines.append(" " * (env_indent_level + 2) + this_line.lstrip("- "))
      else:
        #processed_env_lines.append(" " * env_indent_level + "}")
        processed_env_lines.append(" " * env_indent_level + "env:\n")
        processed_env_lines.append(" " * (this_indent_level) + this_line.lstrip("- "))
    else :
      processed_env_lines.append(" " * (this_indent_level-2) + this_line)

  return original_file_lines[:initial_index + 1] + processed_env_lines + original_file_lines[final_i:]

# Purpose:
#   This function is designed to itterate through the file lines of a k8s YAML that needs to be translated
#   and transforms certain blocks into a more intuitive format for the translation software.
# params:
#    original_file_lines: 
#       An array of the YAML file lines taken from the file that needs to be interpreted
# returns :
#    An array of file lines that is processed
def pre_process(original_file_lines):
  pre_processed_lines = original_file_lines.copy()
  # todo: need to make correct deep copy of original lines.
  for i in range(0, len(original_file_lines)):
      if re.search(r"\s+env:.*", original_file_lines[i]):
        pre_processed_lines = process_env_block(pre_processed_lines, i)
      else:
        continue
  return pre_processed_lines


# Purpose: 
#    This is the main point of entry for this program. It is designed to  in file system at the YAML directory 
#    your in CWD and use it's YAML file contents to scrape Online documentation adn then translate those files 
#    into a set of equivalent .tf files.

def main():
  SCRAPE_URL_1 = "https://registry.terraform.io/v2/providers/hashicorp/kubernetes?include=categories,moved-to,potential-fork-of,provider-versions,top-modules&include=categories%2Cmoved-to%2Cpotential-fork-of%2Cprovider-versions%2Ctop-modules&name=kubernetes&namespace=hashicorp"
  # STEP: identify files that need converting, then feed each of their "kinds"
  YAMLS = os.listdir(os.getcwd() + r"\YAML")
  output_folder = './tf files'
  if os.path.exists(output_folder):
    # clear output folder
    shutil.rmtree(output_folder)
  os.makedirs(output_folder)
  for file_name in YAMLS:
    # tracks the indent level of the read file
    space_stack = [0, 2]
    last_leading_spaces = 2
    # list of lines to write to a file once the translation is done
    write_lines = []
    # holds the type of file that is being processed so we can lookup the translation in dictionary
    kind = None
    # a boolean to track whether the top level 'kind' has been translated
    kind_translated = False
    # holds the version that the user has made their k8s resource
    resource_version = None
    # holds log of dictionary navigation for easy hierarchy traversal
    tree_log = []
    # dicitonary of terraform attributes arranged in tree hierarchy
    attribute_tree = None
    
    #### STEP ====> pre-process YAMLS so it's easier to translate with a uniform set of rules.
    file = open(rf"YAML/{file_name}")
    yaml_file_lines = pre_process(file.readlines())
    file.close()

    for i in range(0, len(yaml_file_lines)):
      if "#" in yaml_file_lines[i] and yaml_file_lines[i].lstrip().index("#") == 0:
        #skip comment lines for now
        continue

      this_line = ''
      # ": " to limit splitting to KV pairs instead of splitting data
      line = yaml_file_lines[i].lstrip("- ")
      if re.match(r"[\s\t]*[\n\r]+", line) is not None:
        #line with no info detected
        continue
      # sepperate split helps avoid oversplitting
      KV_pair = re.split( r":[\s\n]+", line, maxsplit=1)
      KV_pair = [KV_pair[0]] + re.split(r"\s*#", KV_pair[1], maxsplit=1)[:2]
      KV_pair[1] = KV_pair[1].strip().strip('\"')

      # 2 added because terraform has additional indent within "resource" block
      this_leading_spaces = len(yaml_file_lines[i]) - len(yaml_file_lines[i].lstrip("- ")) + 2
      if resource_version is None:
        if KV_pair[0] == "apiVersion":
          resource_version  = KV_pair[1].split("/")[-1]
        else:
          continue
      
      if kind is None:
        #useful file info starts here
        if KV_pair[0] == "kind":
          #TODO: this needs to be camel case
          kind = KV_pair[1].lower()
          if re.search(r"(?<=[a-z])(?=[A-Z])", KV_pair[1]) is not None:
            #convert to snake case when camel case detected
            kind = re.sub(r'(?<=[a-z])(?=[A-Z])', '_', KV_pair[1]).lower()
        else:
          continue
      #### STEP ===> lookup Cached Version or scrape current version
      if attribute_tree is None:
        if cache_resource_trees:
          cache_file = open("resource_cache.json", "w+")
          try:
            cache_json = json.load(cache_file)
            attribute_tree = next((c for c in cache_json if c['kind'] == kind), None)
            # if the file can be loaded, check if it has the current tree
            if attribute_tree is None:
              # if it doesn't have data, then go find it and write to file
              attribute_tree = scrape(
                SCRAPE_URL_1,
                kind,
                resource_version)
              cache_json.append(attribute_tree)
              json.dump(cache_json, cache_file)
          except:
            #fail to load cache? repopulate
            attribute_tree = scrape(
                SCRAPE_URL_1,
                kind,
                resource_version)
            json.dump([attribute_tree], cache_file)
          cache_file.close()
        else:
          attribute_tree = scrape(
            SCRAPE_URL_1,
            kind,
            resource_version)
      #### STEP ====> format file write data
      if this_leading_spaces > last_leading_spaces:
        # an indent has occurred
        space_stack.append(this_leading_spaces)
        # ensure last written line has correct curly brace
        if write_lines[-1].rstrip()[-1] == ":":
          write_lines[-1] = write_lines[-1].rstrip()[:-1].rstrip() + " {"
        else:
          write_lines[-1] = write_lines[-1].rstrip() + " {"
      elif this_leading_spaces < last_leading_spaces:
        # we just went down an indent
        while space_stack[-1] > this_leading_spaces:
          #close current blocks
          tree_log.pop()
          #popping space stack before appending to generate correct indents
          space_stack.pop()
          write_lines.append(' ' * space_stack[-1] + "}")
      
      if KV_pair[0] == "kind" and not kind_translated:
        this_line += fr'resource "kubernetes_{KV_pair[1].lower()}" "REPLACE_ME" {{'
        kind_translated = True
      else :
        this_line += ' ' * this_leading_spaces  # indents
        res = terra_translate(KV_pair,
          attribute_tree,
          tree_log)  # returns a string with ['TERRAFORM_NAME', 'formatted printout']
        this_line += res[1]

      last_leading_spaces = this_leading_spaces
      write_lines.append(this_line)
    
    # for writing closing curly braces at end of translation
    while len(space_stack) > 1:
      space_stack.pop()
      write_lines.append(' ' * space_stack[-1] + "}")

    #### STEP ====> write formatted file to new folder
    tf_file_name = file_name.split(".")
    file = open(f"{output_folder}/{tf_file_name[0]}.tf", "w+")
    for text in write_lines:
      file.write(text + "\n")

    file.close()
  print("DONE!!!")

main()
